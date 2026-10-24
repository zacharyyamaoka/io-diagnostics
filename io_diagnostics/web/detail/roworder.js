// How a list of named rows is organised: dot-notation / "who: what" names parsed into collapsible FOLDERS, ★ PINNED rows on
// top, and a sort inside every folder (severity, A-Z or manual drag), remembered in the page's localStorage per entity kind.
// Loaded from detail/index.json like a layout (it registers no render(), so the Prototype picker never lists it). It knows
// nothing of diagnostics: a row is {id, name, rank} (rank = how bad, higher is worse), so the entity page's State and
// Configuration sections can build the same tree. Zach, 2026-10-24: "Reordering diagnostics, state, configuration... maybe you
// can do it by name or like tview has groupings... dot notation arm.servo.current and you can parse a folder structure from
// that... within the folders... alphabetical? manual? pin to the top?"
//
// API (window.IORowOrder):
//   folderPath(name)                   -> {path: [folder names], leaf}     the normaliser (see below)
//   prefs(kind, section)               -> {sort, pins[], manual{}, closed[]}  one object per (section, entity kind), live
//   plan(rows, kind, section, opts)    -> {pinned: [row], items: [item], sort}   item = {folder:true, id, name, items, rows, rank, depth}
//                                                                                  | {folder:false, row, depth, flat}
//   setSort(kind, section, sort) · togglePin(kind, section, id) · toggleFolder(kind, section, folderId, byDefaultOpen)
//   isOpen(kind, section, folderId) · moveManual(kind, section, parentId, ids)    each saves to localStorage
//   toolbar(kind, section) / star(kind, section, id)   the two bits of HTML (hooks: data-ro-sort, data-ro-pin)
//   dragAttrs(sort, parentId, itemId)  the attributes of a draggable item (manual sort only); wireDrag(container, onMove)
// The normaliser: a name reads "<who>: <what>". The who and the what each split on dots when they are dot-notation
// (word characters and dashes between dots: "arm.servo.current", not "5.15.1.55 installed"); a who of the form
// "<thing> CAN <n>" splits in two ("fdcanusb 272813EF CAN 1" -> fdcanusb 272813EF / CAN 1). The last segment is the row's leaf
// name, the rest are folders. A folder holding one row is not drawn (the row keeps its full name); a single folder that holds
// every row is dropped (the entity's header already says it).
'use strict';
(function (root) {
  const KEY = (kind, section) => `io-diagnostics.rows.${section}.${kind}`;
  const DOTTED = /^[\w-]+(\.[\w-]+)+$/;
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const natural = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });

  function folderPath(name) {
    const text = String(name);
    const at = text.indexOf(': ');
    const who = at > 0 ? text.slice(0, at) : '';
    const what = at > 0 ? text.slice(at + 2) : text;
    const path = [];
    if (who) {
      const can = /^(.*\S)\s+(CAN \d+)$/.exec(who);
      if (DOTTED.test(who)) path.push(...who.split('.'));
      else if (can) path.push(can[1], can[2]);
      else path.push(who);
    }
    let leaf = what;
    if (DOTTED.test(what)) { const parts = what.split('.'); leaf = parts.pop(); path.push(...parts); }
    return { path, leaf };
  }

  // ------------------------------------------------------------------------------------------ prefs ----
  const cache = new Map();
  function prefs(kind, section) {
    const key = KEY(kind, section);
    if (!cache.has(key)) {
      let stored = {};
      try { stored = JSON.parse(localStorage.getItem(key) || '{}') || {}; } catch (_) { /* storage blocked or not JSON */ }
      cache.set(key, { sort: ['severity', 'name', 'manual'].includes(stored.sort) ? stored.sort : 'severity',
        pins: Array.isArray(stored.pins) ? stored.pins : [], manual: stored.manual && typeof stored.manual === 'object' ? stored.manual : {},
        closed: Array.isArray(stored.closed) ? stored.closed : [], opened: Array.isArray(stored.opened) ? stored.opened : [] });
    }
    return cache.get(key);
  }
  function save(kind, section) {
    try { localStorage.setItem(KEY(kind, section), JSON.stringify(prefs(kind, section))); } catch (_) { /* storage blocked: lasts this page only */ }
  }
  const setSort = (kind, section, sort) => { prefs(kind, section).sort = sort; save(kind, section); };
  function togglePin(kind, section, id) {
    const p = prefs(kind, section);
    p.pins = p.pins.includes(id) ? p.pins.filter((pin) => pin !== id) : [...p.pins, id];
    save(kind, section);
  }
  // a folder is open unless the viewer closed it (or, for a folder that starts closed, opened it)
  function isOpen(kind, section, folderId, byDefault = true) {
    const p = prefs(kind, section);
    return p.closed.includes(folderId) ? false : p.opened.includes(folderId) ? true : byDefault;
  }
  function toggleFolder(kind, section, folderId, byDefault = true) {
    const p = prefs(kind, section);
    const open = isOpen(kind, section, folderId, byDefault);
    p.closed = p.closed.filter((id) => id !== folderId);
    p.opened = p.opened.filter((id) => id !== folderId);
    (open ? p.closed : p.opened).push(folderId);
    save(kind, section);
  }
  function moveManual(kind, section, parentId, ids) { prefs(kind, section).manual[parentId] = ids; save(kind, section); }

  // ------------------------------------------------------------------------------------------- plan ----
  // Sort one level's items: severity = worst first then by name; name = A-Z (numbers in order); manual = the viewer's order,
  // anything new after it in severity order.
  function order(items, sort, p, parentId) {
    const rank = (item) => (item.folder ? item.rank : item.row.rank);
    const label = (item) => (item.folder ? item.name : item.row.name);
    const id = (item) => (item.folder ? item.id : item.row.id);
    const bySeverity = (a, b) => (rank(b) - rank(a)) || natural(label(a), label(b));
    if (sort === 'name') return items.slice().sort((a, b) => natural(label(a), label(b)));
    if (sort === 'manual') {
      const saved = p.manual[parentId] || [];
      const at = (item) => { const i = saved.indexOf(id(item)); return i < 0 ? Infinity : i; };
      return items.slice().sort((a, b) => (at(a) - at(b)) || bySeverity(a, b));
    }
    return items.slice().sort(bySeverity);
  }

  function plan(rows, kind, section, opts = {}) {
    const p = prefs(kind, section);
    const sort = opts.sort || p.sort;
    const idPrefix = opts.idPrefix || '';
    const pinned = p.pins.map((id) => rows.find((row) => row.id === id)).filter(Boolean);
    const rest = rows.filter((row) => !p.pins.includes(row.id));
    // build the folder tree
    const root = { folder: true, id: `${idPrefix}`, name: '', items: [], rows: [], rank: -1, depth: -1 };
    for (const row of rest) {
      const { path, leaf } = folderPath(row.name);
      let at = root;
      path.forEach((segment, depth) => {
        const id = `${idPrefix}${path.slice(0, depth + 1).join('/')}`;
        let next = at.items.find((item) => item.folder && item.id === id);
        if (!next) { next = { folder: true, id, name: segment, items: [], rows: [], rank: -1, depth }; at.items.push(next); }
        at = next;
      });
      at.items.push({ folder: false, row, leaf, depth: path.length, flat: false });
    }
    // a folder's rows and worst rank; a folder of one row is not drawn; a lone folder holding everything is dropped
    const finish = (folder) => {
      folder.items = folder.items.map((item) => (item.folder ? finish(item) : item));
      folder.rows = folder.items.flatMap((item) => (item.folder ? item.rows : [item.row]));
      folder.rank = Math.max(-1, ...folder.rows.map((row) => row.rank));
      return folder;
    };
    finish(root);
    // `flat` = drawn with its full name (it sits at the top level, or its one-row folder was not drawn)
    const flatten = (items, depth) => items.flatMap((item) => {
      if (!item.folder) return [{ ...item, depth, flat: depth === 0, leaf: depth === 0 ? item.row.name : item.leaf }];
      if (item.rows.length === 1) return [{ folder: false, row: item.rows[0], leaf: item.rows[0].name, depth, flat: true }];
      return [{ ...item, depth, items: flatten(item.items, depth + 1) }];
    });
    const shift = (items) => items.map((item) => (item.folder ? { ...item, depth: item.depth - 1, items: shift(item.items) } : { ...item, depth: item.depth - 1, flat: false }));
    let items = flatten(root.items, 0);
    if (items.length === 1 && items[0].folder && items[0].rows.length === rest.length) items = shift(items[0].items);
    // flat rows show their full name; rows inside a folder their leaf
    const sortTree = (list, parentId) => order(list, sort, p, parentId).map((item) => (item.folder ? { ...item, items: sortTree(item.items, item.id) } : item));
    return { pinned, items: sortTree(items, idPrefix || '/'), sort, idPrefix };
  }

  // ------------------------------------------------------------------------------------------- html ----
  const SORTS = [['severity', 'Severity'], ['name', 'A–Z'], ['manual', 'Manual']];
  function toolbar(kind, section) {
    const p = prefs(kind, section);
    return `<div class="ro-tb" role="group" aria-label="Order the rows"><span class="ro-l">Order</span>${SORTS.map(([id, text]) => `<button class="ro-b" data-ro-sort="${id}" aria-pressed="${p.sort === id}">${text}</button>`).join('')}`
      + `<span class="ro-hint">${p.sort === 'manual' ? 'drag a row or folder by its handle' : p.pins.length ? `${p.pins.length} pinned` : '★ pins a row to the top'}</span></div>`;
  }
  const star = (kind, section, id) => {
    const on = prefs(kind, section).pins.includes(id);
    return `<button class="ro-star${on ? ' on' : ''}" data-ro-pin="${esc(id)}" aria-pressed="${on}" title="${on ? 'Unpin' : 'Pin to the top'}">${on ? '★' : '☆'}</button>`;
  };
  const dragAttrs = (sort, parentId, itemId) => (sort === 'manual' ? ` draggable="true" data-ro-item="${esc(itemId)}" data-ro-parent="${esc(parentId)}"` : '');
  const handle = (sort) => (sort === 'manual' ? '<span class="ro-grip" aria-hidden="true">⋮⋮</span>' : '');

  // Drag to reorder (manual sort): dropping an item on a sibling of the same parent puts it there; onMove(parentId, orderedIds).
  function wireDrag(container, onMove) {
    if (container.__roDrag) return;
    container.__roDrag = true;
    let dragged = null;
    container.addEventListener('dragstart', (event) => {
      const item = event.target instanceof Element ? event.target.closest('[data-ro-item]') : null;
      if (!item) return;
      dragged = item;
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', item.dataset.roItem);
    });
    container.addEventListener('dragover', (event) => {
      const over = event.target instanceof Element ? event.target.closest('[data-ro-item]') : null;
      if (dragged && over && over !== dragged && over.dataset.roParent === dragged.dataset.roParent) event.preventDefault();
    });
    container.addEventListener('drop', (event) => {
      const over = event.target instanceof Element ? event.target.closest('[data-ro-item]') : null;
      if (!dragged || !over || over === dragged || over.dataset.roParent !== dragged.dataset.roParent) { dragged = null; return; }
      event.preventDefault();
      const parent = dragged.dataset.roParent;
      const siblings = [...container.querySelectorAll('[data-ro-item]')].filter((el) => el.dataset.roParent === parent).map((el) => el.dataset.roItem);
      const from = siblings.indexOf(dragged.dataset.roItem);
      const to = siblings.indexOf(over.dataset.roItem);
      siblings.splice(from, 1);
      siblings.splice(to, 0, dragged.dataset.roItem);
      dragged = null;
      onMove(parent, siblings);
    });
    container.addEventListener('dragend', () => { dragged = null; });
  }

  const CSS = `
.ro-tb { display: flex; align-items: center; gap: 4px; margin: 6px 0 4px; font-size: 11.5px; color: var(--io-faint); }
.ro-l { margin-right: 4px; }
.ro-b { padding: 1px 8px; border: 1px solid var(--io-border-soft); border-radius: var(--io-radius-pill); background: none; color: var(--io-muted); font: inherit; cursor: pointer; }
.ro-b:hover { background: var(--io-hover); }
.ro-b[aria-pressed="true"] { background: var(--io-pressed); color: var(--io-text-strong); border-color: var(--io-border); }
.ro-b:focus-visible, .ro-star:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.ro-hint { margin-left: auto; }
.ro-star { flex: none; width: 18px; height: 18px; padding: 0; border: 0; background: none; color: var(--io-faint); font-size: 13px; line-height: 1; cursor: pointer; border-radius: var(--io-radius-sm); opacity: 0; }
.ro-star.on { opacity: 1; color: var(--io-text-strong); }
*:hover > .ro-star, *:focus-within > .ro-star, .ro-star:focus-visible { opacity: 1; }
.ro-star:hover { color: var(--io-text-strong); background: var(--io-hover); }
.ro-grip { color: var(--io-faint); cursor: grab; letter-spacing: -2px; margin-right: 4px; user-select: none; }
[data-ro-item][draggable="true"] { cursor: grab; }
.ro-fold { display: flex; align-items: center; gap: 8px; width: 100%; padding: 4px 2px; border: 0; background: none; color: var(--io-text-strong); font: inherit; font-size: 12.5px; font-weight: 600; text-align: left; cursor: pointer; border-radius: var(--io-radius-sm); }
.ro-fold:hover { background: var(--io-hover); }
.ro-fold:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.ro-fold .ro-chev { width: 12px; height: 12px; color: var(--io-faint); transition: transform .12s; }
.ro-fold.open .ro-chev { transform: rotate(90deg); }
.ro-fold .shape { width: 12px; height: 12px; }
.ro-folder > .ro-fold { padding-left: calc(2px + var(--depth, 0) * 14px); }
.ro-pinned > .ro-fold { color: var(--io-muted); }
`;
  function install() {
    if (typeof document === 'undefined' || document.querySelector('style[data-layout="roworder"]')) return;
    const style = document.createElement('style');
    style.dataset.layout = 'roworder';
    style.textContent = CSS;
    document.head.appendChild(style);
  }
  install();

  root.IORowOrder = { folderPath, prefs, plan, setSort, togglePin, toggleFolder, isOpen, moveManual, toolbar, star, dragAttrs, handle, wireDrag, natural, install };
})(typeof window !== 'undefined' ? window : globalThis);
