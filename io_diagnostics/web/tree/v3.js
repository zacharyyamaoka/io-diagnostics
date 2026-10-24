// Tree proposal v3 — "Problems first" for IO diagnostics.
//
// Relies only on docs/api-v1.md (the node shape inside GET /api/v1/state). Registers
//   window.IOTreeRenderers['v3'] = {name, description, render(container, apiTree, {onSelect, selectedPath, query?})}
//
// The idea: the first screen answers "what needs me?". Only the paths that contain a problem open by
// themselves; every healthy sibling set at a level folds into ONE muted line ("2 nodes OK", "1 bus OK · 5 empty")
// that unfolds in place. A row that reports a problem itself carries its message on a second, quieter line.
//
// Zach's tree rules (2026-10-22), and where each one lives below:
//   1. chevrons only on hover, Notion-style over the type icon       -> leadHtml(), .v3-lead in v3.css
//   2. type icon + name together; status shape at the far RIGHT      -> rowHtml(): lead, name, id, hint, status
//   3. problems are SHAPES: triangle / octagon-x / clock / ?         -> SHAPES
//   4. quiet when OK: no green anywhere, an OK row carries no mark   -> statusHtml() draws nothing for ok / null
//   5. not connected = fully grey; connected = normal grey text      -> .v3-off / .v3-item colours in v3.css
//   6. black box: a row shows its OWN level; the rollup is a quiet   -> hintHtml(): "child in error" beside the
//      hint (spider WARN over a leg ERROR)                               self-report, never replacing it
'use strict';
(function () {
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const WORST_FIRST = ['error', 'stale', 'unknown', 'warn'];
  const WORD = { error: 'error', stale: 'stale', unknown: 'unknown', warn: 'warning' };
  const WORDS = { error: 'errors', stale: 'stale', unknown: 'unknown', warn: 'warnings' };
  const CHILD_IN = { error: 'child in error', stale: 'child stale', unknown: 'child unknown', warn: 'child warning' };

  // ------------------------------------------------------------------------------ icons ----
  // Type icons: 24-unit stroke drawings in the same family as web/index.html's sprite, carried here so the
  // renderer is self-contained (a host page may have no sprite).
  const TYPE_ICONS = {
    host: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    group: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    usb: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>',
    pci: '<rect x="3" y="6" width="18" height="10" rx="1.5"/><path d="M6 16v3M9 16v3M12 16v3M15 16v3M18 16v3M7 10h4"/>',
    net: '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-3h14v3"/>',
    tty: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3M11 15h5"/>',
    video: '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="m16 10 6-3v10l-6-3"/>',
    port: '<rect x="3" y="7" width="18" height="10" rx="1.5"/><path d="M7 11h3M14 11h3"/>',
    hub: '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8"/>',
    adapter: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    bus: '<path d="M3 17h18M6 17v-6M12 17v-6M18 17v-6"/><circle cx="6" cy="8" r="2"/><circle cx="12" cy="8" r="2"/><circle cx="18" cy="8" r="2"/>',
    servo: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/><path d="M12 4v3M20 12h-3M12 20v-3M4 12h3"/>',
    chip: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
    motor: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    stream: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    webcam: '<circle cx="12" cy="10" r="8"/><circle cx="12" cy="10" r="3"/><path d="M7 22h10M12 22v-4"/>',
    hid: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M8 13h.01M12 13h.01M16 13h.01M7 16h10"/>',
    device: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="12" cy="12" r="2"/>',
  };
  // the folded "N OK" line: a stack of layers (this many things, folded away)
  const QUIET_ICON = '<path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 13 9 5 9-5"/>';
  const CHEVRON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg>';

  // Status SHAPES on a 16-unit grid (rule 3): the shape alone says the level, colour only reinforces it.
  //   warn = triangle with "!", error = octagon with "x", stale = clock, unknown = circled "?"
  const SHAPES = {
    warn: '<svg class="v3-shape" viewBox="0 0 16 16" aria-hidden="true"><path class="v3-s-body" d="M8 1.9c.4 0 .75.2.95.55l5.9 10.3c.4.7-.1 1.55-.95 1.55H2.1c-.85 0-1.35-.85-.95-1.55l5.9-10.3c.2-.35.55-.55.95-.55Z"/><path class="v3-s-mark" d="M8 6.1v3.3"/><circle class="v3-s-dot" cx="8" cy="11.8" r=".95"/></svg>',
    error: '<svg class="v3-shape" viewBox="0 0 16 16" aria-hidden="true"><path class="v3-s-body" d="M4.75 1.2h6.5l3.55 3.55v6.5l-3.55 3.55h-6.5L1.2 11.25v-6.5Z"/><path class="v3-s-mark" d="m5.75 5.75 4.5 4.5m0-4.5-4.5 4.5"/></svg>',
    stale: '<svg class="v3-shape v3-line-shape" viewBox="0 0 16 16" aria-hidden="true"><circle class="v3-s-ring" cx="8" cy="8" r="6.1"/><path class="v3-s-hand" d="M8 4.6V8l2.4 1.6"/></svg>',
    unknown: '<svg class="v3-shape v3-line-shape" viewBox="0 0 16 16" aria-hidden="true"><circle class="v3-s-ring" cx="8" cy="8" r="6.1"/><path class="v3-s-hand" d="M6.3 6.35a1.75 1.75 0 1 1 2.45 1.6c-.45.2-.75.55-.75 1.05v.4"/><circle class="v3-s-dot" cx="8" cy="11.4" r=".85"/></svg>',
  };

  // --------------------------------------------------------------------------- helpers ----
  const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const typeIcon = (name) => `<svg class="v3-type" viewBox="0 0 24 24" aria-hidden="true">${TYPE_ICONS[name] || TYPE_ICONS.device}</svg>`;
  const glyph = (level) => `<span class="v3-glyph lv-${level}">${SHAPES[level]}</span>`;
  const kids = (node) => node.children || [];
  const isGrouping = (node) => !node.own || node.own.level === null || node.own.level === undefined;
  // the row's own report is a problem AND it is an independent cause (not explained by a failed ancestor)
  const ownCause = (node) => !isGrouping(node) && PROBLEM.has(node.own.level) && !node.knock_on_of;
  const subtreeHasCause = (node) => PROBLEM.has(node.rollup && node.rollup.level) || (PROBLEM.has(node.level) && !node.knock_on_of);

  // independent causes strictly BELOW this row: rollup.counts counts "at or below", so take the row's own off
  function causesBelow(node) {
    const counts = (node.rollup && node.rollup.counts) || {};
    const total = WORST_FIRST.reduce((sum, level) => sum + (counts[level] || 0), 0);
    return Math.max(0, total - (ownCause(node) ? 1 : 0));
  }

  // what the user's expand/collapse was decided against; when this changes the decision lapses
  const problemSignature = (node) => `${(node.rollup && node.rollup.level) || ''}|${(node.rollup && node.rollup.origin) || ''}|${causesBelow(node)}`;

  function noun(node, count) {
    const plural = count !== 1;
    if (node.kind === 'port') return plural ? 'ports' : 'port';
    if (node.kind === 'bus') return plural ? 'buses' : 'bus';
    if (node.kind === 'node') return plural ? 'nodes' : 'node';
    if (node.kind === 'part') return plural ? 'parts' : 'part';
    if (node.kind === 'stream') return plural ? 'streams' : 'stream';
    if (node.kind === 'group') return plural ? 'groups' : 'group';
    if (node.icon === 'video') return plural ? 'video nodes' : 'video node';
    if (node.icon === 'tty') return plural ? 'serial ports' : 'serial port';
    if (node.icon === 'net') return plural ? 'interfaces' : 'interface';
    if (node.icon === 'hub') return plural ? 'hubs' : 'hub';
    if (node.icon === 'camera' || node.icon === 'webcam') return plural ? 'cameras' : 'camera';
    return plural ? 'devices' : 'device';
  }

  // "2 nodes OK", "6 video nodes · 2 streams OK", "1 bus OK · 5 empty", "4 empty ports"
  function quietText(members) {
    const ok = [];
    const empty = [];
    const silent = [];
    for (const member of members) {
      if (member.connected === false || (member.level == null && !kids(member).length)) empty.push(member);
      else if (member.level == null) silent.push(member);
      else ok.push(member);
    }
    const byNoun = (list) => {
      const groups = new Map();
      for (const member of list) {
        const key = noun(member, 1);
        groups.set(key, (groups.get(key) || []).concat(member));
      }
      return [...groups.values()];
    };
    const parts = [];
    if (ok.length) {
      const groups = byNoun(ok);
      parts.push(groups.length <= 2
        ? `${groups.map((g) => `${g.length} ${noun(g[0], g.length)}`).join(' · ')} OK`
        : `${ok.length} OK`);
    }
    if (empty.length) {
      const groups = byNoun(empty);
      parts.push(!ok.length && groups.length === 1 ? `${empty.length} empty ${noun(empty[0], empty.length)}` : `${empty.length} empty`);
    }
    if (silent.length) parts.push(`${silent.length} not reporting`);
    return parts.join(' · ');
  }

  // ---------------------------------------------------------------------------- memory ----
  // One tree per page, so the user's choices live at module level and survive the 1 s re-render and a switch
  // to another proposal and back.
  const memory = {
    open: new Map(),      // path -> {open, signature}: an explicit expand/collapse
    quietOpen: new Set(), // parent paths whose folded "N OK" line the user unfolded
  };
  const mounts = new WeakMap(); // container -> mount

  // Problems first: a row opens by itself only when an independent cause sits somewhere below it.
  const defaultOpen = (node) => node.depth === 0 || !node.parent || causesBelow(node) > 0;

  function isOpen(node) {
    const choice = memory.open.get(node.path);
    // WHY the signature: a collapse decided against yesterday's problem must not hide a new fault below it;
    // when the worst cause below changes, the path falls back to "problems open themselves".
    if (choice && choice.signature === problemSignature(node)) return choice.open;
    if (choice) memory.open.delete(node.path);
    return defaultOpen(node);
  }
  const setOpen = (node, open) => memory.open.set(node.path, { open, signature: problemSignature(node) });

  function classify(child) {
    if (subtreeHasCause(child)) return 'problem';
    if (PROBLEM.has(child.level)) return 'knock';  // not OK, but explained by a failed ancestor: never folded as OK
    return 'quiet';
  }

  // ------------------------------------------------------------------------- flattening ----
  function indexTree(tree) {
    const byPath = new Map();
    (function visit(node) { byPath.set(node.path, node); kids(node).forEach(visit); })(tree);
    return byPath;
  }

  function matches(node, query) {
    return [node.name, node.id_text, node.path, node.message, node.hardware_id].some((v) => String(v || '').toLowerCase().includes(query));
  }

  function flatten(tree, query) {
    const items = [];
    if (query) {
      // Searching: show every hit with its ancestors, fully open; folding would hide what was asked for.
      const keep = new Set();
      (function mark(node, trail) {
        if (matches(node, query)) { trail.forEach((p) => keep.add(p)); keep.add(node.path); }
        kids(node).forEach((child) => mark(child, trail.concat(node.path)));
      })(tree, []);
      (function walk(node) {
        if (!keep.has(node.path)) return;
        const shown = kids(node).filter((c) => keep.has(c.path));
        items.push({ type: 'row', node, open: shown.length > 0, hit: matches(node, query) });
        shown.forEach(walk);
      })(tree);
      return items;
    }
    (function walk(node) {
      const open = kids(node).length > 0 && isOpen(node);
      items.push({ type: 'row', node, open });
      if (!open) return;
      const groups = { problem: [], knock: [], quiet: [] };
      kids(node).forEach((child) => groups[classify(child)].push(child));
      // problems first (in the host's order), then the explained knock-ons, then one folded line for the rest
      groups.problem.forEach(walk);
      groups.knock.forEach(walk);
      if (groups.quiet.length === 1) {
        walk(groups.quiet[0]); // folding one row into a line saves nothing and hides its name
      } else if (groups.quiet.length > 1) {
        const unfolded = memory.quietOpen.has(node.path);
        items.push({ type: 'quiet', parent: node, members: groups.quiet, open: unfolded, depth: groups.quiet[0].depth });
        if (unfolded) groups.quiet.forEach(walk);
      }
    })(tree);
    return items;
  }

  // Make a selection that arrived from outside (search, activity link, chip) visible: open its ancestors and
  // unfold the "N OK" line it sits in.
  function reveal(byPath, path) {
    let node = byPath.get(path);
    while (node && node.parent) {
      const parent = byPath.get(node.parent);
      if (!parent) break;
      if (!isOpen(parent)) setOpen(parent, true);
      if (classify(node) === 'quiet' && kids(parent).filter((c) => classify(c) === 'quiet').length > 1) memory.quietOpen.add(parent.path);
      node = parent;
    }
  }

  // ---------------------------------------------------------------------------- markup ----
  function leadHtml(icon, toggleKey) {
    // Notion-style: the type icon IS the disclosure target; on hover it turns into the chevron (rule 1)
    const chevron = toggleKey ? `<button class="v3-chev" type="button" tabindex="-1" ${toggleKey} aria-label="Expand or collapse">${CHEVRON}</button>` : '';
    return `<span class="v3-lead">${icon}${chevron}</span>`;
  }

  function statusHtml(node, byPath) {
    const level = node.level;
    if (!PROBLEM.has(level)) return '<span class="v3-status"></span>'; // rule 4: OK and "nothing reports" draw nothing
    let title;
    const extra = [];
    if (node.knock_on_of) {
      const cause = byPath.get(node.knock_on_of);
      extra.push('v3-knock');
      title = `${WORD[level]}, explained by ${cause ? cause.name : node.knock_on_of} above (counted once, as that cause)`;
    } else if (isGrouping(node)) {
      // a pure grouping row has no report of its own: it shows the worst below, drawn hollow ("contains")
      extra.push('v3-hollow');
      title = `contains ${WORD[level]}: ${(node.rollup && node.rollup.message) || node.message || ''}`;
    } else {
      title = `${WORD[level]}: ${node.message || ''}`;
    }
    return `<span class="v3-status lv-${level} ${extra.join(' ')}" title="${esc(title)}">${SHAPES[level]}</span>`;
  }

  function hintHtml(node, open, byPath) {
    const hint = node.hint;
    if (hint && PROBLEM.has(hint.level)) {
      const origin = byPath.get(hint.origin);
      const direct = Boolean(origin && origin.parent === node.path);
      // Rule 6, kept quiet: an open row whose problem is visible right below needs no hint, EXCEPT the black-box
      // case where the cause is one of its own parts (CAN 15 says OK, its driver part is in ERROR).
      if (!open || (direct && origin.edge === 'part_of')) {
        // WHY no count (Zach, 2026-10-24): a roll-up is the worst level below and its icon, not how many
        const text = direct ? CHILD_IN[hint.level] : `${WORD[hint.level]} below`;
        return `<span class="v3-hint" title="${esc(hint.message || '')}">${glyph(hint.level)}${esc(text)}</span>`;
      }
      return '';
    }
    if (!open && kids(node).length) {
      // folded away by the user: say something is down there even when it is no worse than this row
      const below = causesBelow(node);
      if (below) return '<span class="v3-hint v3-hint-plain">more below</span>';
      const knockOn = (node.rollup && node.rollup.knock_on && node.rollup.knock_on.length) || 0;
      if (knockOn) return `<span class="v3-hint v3-hint-plain" title="problems explained by this row's own failure">${knockOn} knock-on</span>`;
    }
    return '';
  }

  function rowHtml(item, ctx) {
    const node = item.node;
    const hasKids = kids(node).length > 0;
    const off = node.connected === false;
    const selfProblem = !isGrouping(node) && PROBLEM.has(node.own.level);
    const classes = ['v3-item', 'v3-row', hasKids ? 'v3-kids' : '', item.open ? 'v3-open' : '', off ? 'v3-off' : '',
      node.knock_on_of ? 'v3-knocked' : '', selfProblem && !node.knock_on_of ? 'v3-problem' : '',
      node.path === ctx.selected ? 'v3-sel' : '', item.hit ? 'v3-hit' : ''].filter(Boolean).join(' ');
    const toggle = hasKids && !ctx.query ? `data-v3-toggle="${esc(node.path)}"` : '';
    let message = '';
    if (selfProblem) {
      // the self-report's reason, on a quieter second line: the tree answers "what is wrong" without a click
      const cause = node.knock_on_of ? ctx.byPath.get(node.knock_on_of) : null;
      const prefix = cause ? `↳ ${cause.name} · ` : '';
      message = `<div class="v3-msg">${esc(prefix + (node.own.message || node.message || ''))}</div>`;
    }
    const title = [node.name, node.level ? `${WORD[node.level] || node.level}: ${node.message || ''}` : '', off ? 'not connected' : '']
      .filter(Boolean).join('\n');
    return `<div class="${classes}" role="treeitem" aria-level="${node.depth + 1}" aria-selected="${node.path === ctx.selected}"`
      + `${hasKids ? ` aria-expanded="${item.open}"` : ''} data-v3-path="${esc(node.path)}" style="--d:${node.depth}" title="${esc(title)}">`
      + `<div class="v3-line">${leadHtml(typeIcon(node.icon), toggle)}<span class="v3-name">${esc(node.name)}</span>`
      + `<span class="v3-id">${esc(node.id_text || '')}</span>${hintHtml(node, item.open, ctx.byPath)}${statusHtml(node, ctx.byPath)}</div>`
      + `${message}</div>`;
  }

  function quietHtml(item) {
    const names = item.members.map((m) => m.name);
    const title = `${names.slice(0, 14).join('\n')}${names.length > 14 ? `\n… ${names.length - 14} more` : ''}`;
    const toggle = `data-v3-quiet="${esc(item.parent.path)}"`;
    return `<div class="v3-item v3-quiet ${item.open ? 'v3-open' : ''}" role="treeitem" aria-level="${item.depth + 1}"`
      + ` aria-expanded="${item.open}" ${toggle} style="--d:${item.depth}" title="${esc(title)}">`
      + `<div class="v3-line">${leadHtml(`<svg class="v3-type" viewBox="0 0 24 24" aria-hidden="true">${QUIET_ICON}</svg>`, toggle)}`
      + `<span class="v3-qtext">${esc(quietText(item.members))}</span>${item.open ? '<span class="v3-qfold">fold</span>' : ''}</div></div>`;
  }

  function headHtml(tree, byPath) {
    const counts = (tree.rollup && tree.rollup.counts) || {};
    const shown = WORST_FIRST.filter((level) => counts[level]);
    const right = shown.length
      ? shown.map((level) => `<span class="v3-count" title="${counts[level]} independent ${counts[level] === 1 ? WORD[level] : WORDS[level]}">${glyph(level)}${counts[level]}</span>`).join('')
      : '<span class="v3-allok">nothing needs you</span>';
    return `<div class="v3-head"><span>Entities · ${byPath.size}</span><span class="v3-counts">${right}</span></div>`;
  }

  // ---------------------------------------------------------------------------- render ----
  function draw(mount) {
    const tree = mount.tree;
    let html;
    if (!tree || !tree.path) {
      html = '<div class="v3-empty">Waiting for the host…</div>';
      mount.items = [];
    } else {
      const query = String(mount.options.query || '').trim().toLowerCase();
      const ctx = { byPath: mount.byPath, selected: mount.selected, query };
      mount.items = flatten(tree, query);
      html = headHtml(tree, mount.byPath)
        + mount.items.map((item) => (item.type === 'row' ? rowHtml(item, ctx) : quietHtml(item))).join('')
        + (query && mount.items.length === 0 ? '<div class="v3-empty">No match.</div>' : '');
    }
    // WHY compare: the page re-renders every second; untouched markup keeps hover, focus and scroll steady
    if (html !== mount.html) {
      mount.root.innerHTML = html;
      mount.html = html;
    }
  }

  function select(mount, path) {
    mount.selected = path;
    mount.selectedSeen = path;
    draw(mount);
    const row = mount.root.querySelector('.v3-row.v3-sel');
    if (row) row.scrollIntoView({ block: 'nearest' });
    if (mount.options.onSelect) mount.options.onSelect(path);
  }

  function toggle(mount, path) {
    const node = mount.byPath.get(path);
    if (!node) return;
    setOpen(node, !isOpen(node));
    draw(mount);
  }

  function wire(mount) {
    const root = mount.root;
    root.addEventListener('click', (event) => {
      const chevron = event.target.closest('[data-v3-toggle]');
      if (chevron && chevron.classList.contains('v3-chev')) return toggle(mount, chevron.dataset.v3Toggle);
      const quiet = event.target.closest('[data-v3-quiet]');
      if (quiet) {
        const path = quiet.dataset.v3Quiet;
        if (memory.quietOpen.has(path)) memory.quietOpen.delete(path); else memory.quietOpen.add(path);
        return draw(mount);
      }
      const row = event.target.closest('[data-v3-path]');
      if (row) select(mount, row.dataset.v3Path);
    });
    root.addEventListener('dblclick', (event) => { // IDE habit: double-click a row folds or unfolds it
      const row = event.target.closest('.v3-row.v3-kids');
      if (row && !event.target.closest('.v3-chev')) toggle(mount, row.dataset.v3Path);
    });
    root.addEventListener('keydown', (event) => {
      const rows = mount.items.filter((item) => item.type === 'row');
      const at = rows.findIndex((item) => item.node.path === mount.selected);
      const current = rows[at];
      const go = (item) => { if (item) select(mount, item.node.path); };
      if (event.key === 'ArrowDown') go(rows[Math.min(rows.length - 1, at + 1)] || rows[0]);
      else if (event.key === 'ArrowUp') go(rows[Math.max(0, at - 1)] || rows[0]);
      else if (event.key === 'ArrowRight' && current) {
        if (kids(current.node).length && !current.open) toggle(mount, current.node.path);
        else if (current.open) go(rows[at + 1]);
      } else if (event.key === 'ArrowLeft' && current) {
        if (current.open && current.node.depth > 0) toggle(mount, current.node.path);
        else if (current.node.parent) select(mount, current.node.parent);
      } else return;
      event.preventDefault();
    });
  }

  function render(container, apiTree, options) {
    if (!container) return;
    let mount = mounts.get(container);
    if (!mount) {
      const root = document.createElement('div');
      root.className = 'iot-v3';
      root.setAttribute('role', 'tree');
      root.setAttribute('aria-label', 'IO entities, problems first');
      root.tabIndex = 0;
      mount = { root, html: null, selected: null, selectedSeen: undefined, items: [] };
      mounts.set(container, mount);
      wire(mount);
    }
    if (mount.root.parentNode !== container) { // first render, or another proposal drew here in between
      container.replaceChildren(mount.root);
      mount.html = null;
    }
    mount.options = options || {};
    // accept the whole /api/v1/state as well as its .tree
    mount.tree = apiTree && apiTree.tree && apiTree.api ? apiTree.tree : apiTree;
    mount.byPath = mount.tree && mount.tree.path ? indexTree(mount.tree) : new Map();
    const wanted = mount.options.selectedPath;
    if (wanted !== undefined && wanted !== mount.selectedSeen) {
      mount.selectedSeen = wanted;
      mount.selected = wanted;
      if (wanted) reveal(mount.byPath, wanted);
    }
    draw(mount);
  }

  // the stylesheet sits beside this file; link it once unless the page already did (the switcher may)
  (function linkStylesheet() {
    const script = document.currentScript;
    if (!script || document.querySelector('link[href$="tree/v3.css"]')) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = new URL('v3.css', script.src).href;
    document.head.appendChild(link);
  })();

  window.IOTreeRenderers = window.IOTreeRenderers || {};
  window.IOTreeRenderers.v3 = {
    name: 'Problems first',
    description: 'Only the paths that hold a problem open by themselves; each level\'s healthy rows fold into one muted "N OK" line; a black box shows its own level with a quiet "child in error" hint.',
    render,
  };
})();
