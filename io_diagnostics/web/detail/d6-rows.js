// window.IOEntityRows: the entity page's shared ROW MODEL (docs/state-config-v1.md section 9) and the Configuration tab's editing
// (section 8). Loaded by index.html before the layouts; d6.js draws with it, nothing else does.
//
// ONE row model for Diagnostics, State and Configuration (Zach, 2026-10-24, "# COnfiguration page"):
//  - a row named in dot notation (servo.max_current_A, motor_position.rotor_to_output_ratio, arm.servo.current) is a leaf in a
//    folder tree, like tview's config tree; a name with spaces, a colon or no dot stays at the top level;
//  - order inside a folder: alphabetical (folders first), or MANUAL (drag a row by its handle), and starred rows first as a
//    "Pinned" block; Diagnostics may also sort by severity (its default);
//  - Diagnostics names are "who: what" ("fdcanusb 272813EF CAN 1: bus"): `folderPath` splits them into the same folders, and `tidy` leaves a
//    folder of one row undrawn; the Diagnostics variants (d1a-d1e, detail/d1x.js) draw through build() / renderTree() like State and Configuration
//    (Zach, 2026-10-24: one row model; the variants lane's own IORowOrder, localStorage-only, is gone);
//  - pins, manual order, folded folders and the sort are one arrangement per entity KIND and section, kept by the host beside
//    settings.toml (GET / POST /api/v1/arrangements), so every CAN board shares one; when the host cannot keep it (an older
//    host, a read-only home) the page keeps it in this browser's localStorage and says so.
// Editing (Configuration tab): one confirm opens an edit session on the board (a heartbeat keeps it); Enter applies one setting
// LIVE; each row says plainly pending, accepted, rejected (and what the board kept) or not saved; Save keeps every live change on
// the board, Revert all puts them back; a row resets to the stored value, the deployment record's, or the default; a safety key
// asks a second confirm and the host refuses it unless the board is stopped. The host enforces all of it; this file only asks.
'use strict';
(function (root) {
  const esc = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const LS = 'io-diagnostics.d6.arrangements';
  const BEAT_MS = 3000;
  const STOP_AFTER_MS = 3500;
  const PAGE_HEADERS = { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' };
  const DOTTED = /^[A-Za-z0-9_\-[\]]+(\.[A-Za-z0-9_\-[\]]+)+$/;
  const SORTS = { name: 'A–Z', manual: 'Manual', severity: 'Severity' };
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const natural = (a, b) => String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' });

  // ------------------------------------------------------------------------------ the arrangement ----
  const A = { data: {}, loaded: false, file: null, note: '', local: false, onChange: null, started: false };
  async function loadArrangements(onChange) {
    A.onChange = onChange || A.onChange;
    if (A.started) return;
    A.started = true;
    try {
      const response = await fetch('/api/v1/arrangements', { cache: 'no-store', signal: AbortSignal.timeout(8000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const body = await response.json();
      A.data = body.arrangements || {};
      A.file = body.file;
      A.note = body.note || '';
      A.local = false;
    } catch (_) {
      A.local = true; // the host keeps none: this browser does
      try { A.data = JSON.parse(localStorage.getItem(LS) || '{}'); } catch (e) { A.data = {}; }
    }
    A.loaded = true;
    if (A.onChange) A.onChange();
  }
  const spec = (kind, section) => ({ pins: [], order: {}, folded: [], sort: '', ...((A.data[kind] || {})[section] || {}) });
  async function save(kind, section, value) {
    const next = { ...value };
    if (!next.sort) delete next.sort; // an unchosen order is the section's default, not a value
    A.data[kind] = { ...(A.data[kind] || {}), [section]: next };
    if (A.onChange) A.onChange();
    if (!A.local) {
      try {
        const response = await fetch('/api/v1/arrangements', { method: 'POST', headers: PAGE_HEADERS, body: JSON.stringify({ kind, section, value: next }) });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        A.note = (await response.json()).note || '';
        return;
      } catch (_) { A.local = true; }
    }
    try { localStorage.setItem(LS, JSON.stringify(A.data)); } catch (_) { /* private mode: lasts this page only */ }
  }
  const update = (kind, section, patch) => save(kind, section, { ...spec(kind, section), ...patch });

  // ------------------------------------------------------------------------------------- the tree ----
  const segments = (name) => (DOTTED.test(String(name)) ? String(name).split('.') : [String(name)]);
  const defaultSplit = (name) => { const parts = segments(name); return { path: parts.slice(0, -1), leaf: parts[parts.length - 1] }; };

  // A Diagnostics name reads "<who>: <what>". The who and the what each split on dots when they are dot-notation ("arm.servo.current", not
  // "5.15.1.55 installed"); a who of the form "<thing> CAN <n>" splits in two ("fdcanusb 272813EF CAN 1" -> fdcanusb 272813EF / CAN 1). The last
  // segment is the row's leaf name, the rest are folders.
  const DIAG_DOTTED = /^[\w-]+(\.[\w-]+)+$/;
  function folderPath(name) {
    const text = String(name);
    const at = text.indexOf(': ');
    const who = at > 0 ? text.slice(0, at) : '';
    const what = at > 0 ? text.slice(at + 2) : text;
    const path = [];
    if (who) {
      const can = /^(.*\S)\s+(CAN \d+)$/.exec(who);
      if (DIAG_DOTTED.test(who)) path.push(...who.split('.'));
      else if (can) path.push(can[1], can[2]);
      else path.push(who);
    }
    let leaf = what;
    if (DIAG_DOTTED.test(what)) { const parts = what.split('.'); leaf = parts.pop(); path.push(...parts); }
    return { path, leaf };
  }

  // items -> {pinned: [item], root: folder, sp, mode}; folder = {type: 'folder', name, path, children: [folder | leaf]}, leaf = {type: 'leaf', name, item}
  //   split(item) -> {path: [folder names], leaf}     how a name becomes folders (default: dot notation)
  //   idPrefix                                       keeps two plans on one page (a group's own) from sharing folder ids
  //   tidy                                           a folder holding ONE row is not drawn (the row keeps its full name), and a single folder holding every
  //                                                  row is dropped (the entity's header already says it): Diagnostics
  function build(items, { idOf, nameOf, split, sp, mode, severityOf, idPrefix = '', tidy = false }) {
    const pinned = sp.pins.map((id) => items.find((item) => idOf(item) === id)).filter(Boolean);
    const pinnedIds = new Set(pinned.map(idOf));
    const rest = items.filter((item) => !pinnedIds.has(idOf(item)));
    const root = { type: 'folder', name: '', path: '', children: [] };
    const join = (parts) => idPrefix + parts.join(split ? '/' : '.');
    for (const item of rest) {
      const { path: folders, leaf } = (split || ((i) => defaultSplit(nameOf(i))))(item);
      let folder = root;
      folders.forEach((part, at) => {
        let next = folder.children.find((child) => child.type === 'folder' && child.name === part);
        if (!next) { next = { type: 'folder', name: part, path: join(folders.slice(0, at + 1)), children: [] }; folder.children.push(next); }
        folder = next;
      });
      folder.children.push({ type: 'leaf', name: leaf, item });
    }
    if (tidy) {
      const only = (folder) => (folder.children[0].type === 'leaf' ? folder.children[0] : only(folder.children[0]));
      const tidyTree = (folder) => {
        folder.children = folder.children.map((child) => {
          if (child.type === 'leaf') return child;
          if (countLeaves(child) === 1) { const one = only(child); return { type: 'leaf', name: nameOf(one.item), item: one.item }; }
          return tidyTree(child);
        });
        return folder;
      };
      tidyTree(root);
      if (root.children.length === 1 && root.children[0].type === 'folder' && countLeaves(root.children[0]) === rest.length) root.children = root.children[0].children;
    }
    const severity = (node) => (node.type === 'leaf' ? (severityOf ? severityOf(node.item) : 0) : Math.max(0, ...node.children.map(severity)));
    const sort = (folder) => {
      const manual = sp.order[folder.path] || [];
      folder.children.sort((a, b) => {
        if (mode === 'manual' && (manual.includes(a.name) || manual.includes(b.name))) {
          const ia = manual.includes(a.name) ? manual.indexOf(a.name) : 1e9;
          const ib = manual.includes(b.name) ? manual.indexOf(b.name) : 1e9;
          return (ia - ib) || natural(a.name, b.name);
        }
        if (mode === 'severity' && severity(a) !== severity(b)) return severity(b) - severity(a);
        if (a.type !== b.type) return a.type === 'folder' ? -1 : 1;
        return natural(a.name, b.name);
      });
      folder.children.filter((child) => child.type === 'folder').forEach(sort);
    };
    sort(root);
    return { pinned, root, sp, mode };
  }
  const countLeaves = (folder) => folder.children.reduce((n, child) => n + (child.type === 'leaf' ? 1 : countLeaves(child)), 0);

  // The page's own marks on a row: its star (pin) and its drag handle. `id` is what the arrangement stores.
  const star = (id, on) => `<button class="er-pin${on ? ' on' : ''}" data-er-pin="${esc(id)}" aria-pressed="${on}" title="${on ? 'Unpin' : 'Pin to the top'}" aria-label="${on ? 'Unpin' : 'Pin'} ${esc(id)}">${on ? '★' : '☆'}</button>`;
  const handle = (parent, name) => `<span class="er-drag" draggable="true" data-er-drag data-er-parent="${esc(parent)}" data-er-id="${esc(name)}" title="Drag to reorder" aria-hidden="true">⋮⋮</span>`;
  const rowAttrs = (parent, name, section) => `data-er-row data-er-parent="${esc(parent)}" data-er-id="${esc(name)}" data-er-section="${section}"`;

  // renders the pinned block and the tree. leaf(item, {label, depth, parent, id, pinned}) draws one row (its own markup, with the
  // marks from star() / handle() and rowAttrs()); the folders are drawn here.
  // mark(folder) -> html: what a folder shows at its end instead of the number of rows (Diagnostics: the worst level below it, as an icon)
  function renderTree(result, { section, sp, leaf, mark, windowRows }) {
    const folded = new Set(sp.folded);
    const out = [];
    if (result.pinned.length) {
      out.push(`<div class="er-pinned" data-er-pinned="${section}"><h4 class="er-pinh">★ Pinned</h4>${result.pinned.map((item) => leaf(item, { label: null, depth: 0, parent: '__pins__', id: null, pinned: true })).join('')}</div>`);
    }
    const walk = (folder, depth) => {
      for (const child of folder.children) {
        if (child.type === 'leaf') { out.push(() => leaf(child.item, { label: child.name, depth, parent: folder.path, id: child.name, pinned: false })); continue; }
        // a chain of folders that each hold one folder (aux1 > pins > 0) is one row: aux1.pins.0
        let node = child;
        let label = child.name;
        while (node.children.length === 1 && node.children[0].type === 'folder') { node = node.children[0]; label += `.${node.name}`; }
        const open = !folded.has(node.path);
        out.push(() => `<div class="er-fold" ${rowAttrs(folder.path, child.name, section)} style="--er-depth:${depth}"><button class="er-fb" data-er-fold="${esc(node.path)}" aria-expanded="${open}">`
          + `<svg class="ic sec-chev"><use href="#i-chev"/></svg><span class="er-fn">${esc(label)}</span>${mark ? mark(node) : `<span class="er-fc">${countLeaves(node)}</span>`}</button>${handle(folder.path, child.name)}</div>`);
        if (open) walk(node, depth + 1);
      }
    };
    walk(result.root, 0);
    // WHY: large State lists supply a viewport window before any leaf builds its sparkline or markup.
    const draw = (row) => typeof row === 'function' ? row() : row;
    return windowRows ? windowRows(out.map((row) => () => draw(row))) : out.map(draw).join('');
  }

  // the little order switch every tab carries: A–Z · Manual (· Severity for Diagnostics), and where the arrangement lives
  function sortControl(section, sp, modes, defaultMode) {
    const mode = sp.sort || defaultMode;
    const where = A.local ? 'kept in this browser: the host keeps no arrangements' : A.file ? `kept in ${A.file} (every ${'board of this kind'} shares it)` : 'kept for this run only (the host has no config file)';
    return `<label class="er-sort" title="${esc(where)}"><span>Order</span><select data-er-sort="${section}" aria-label="Order of the rows">${modes.map((m) => `<option value="${m}"${m === mode ? ' selected' : ''}>${SORTS[m]}</option>`).join('')}</select></label>`;
  }

  // The shared row model's own styles: folders, stars, handles, the order switch. WHY here and not in d6.js: the Diagnostics variants
  // (d1a-d1e) draw rows through this model too and are also whole pages of their own (the Prototype picker), where d6.js never draws.
  const CSS = `
.er-ctl { display: inline-flex; align-items: center; gap: 2px; margin-right: 4px; vertical-align: middle; }
.er-pin { background: transparent; border: 0; padding: 0 2px; color: var(--io-faint); cursor: pointer; font-size: 13px; line-height: 1; opacity: 0; }
.er-pin.on { opacity: 1; color: var(--io-text-strong); }
.d6-r:hover .er-pin, .d6-sr:hover .er-pin, .er-cfgrow:hover .er-pin, .dx-r:hover .er-pin, .dx-lane:hover .er-pin, .er-pin:focus-visible { opacity: 1; }
.er-drag { color: var(--io-faint); cursor: grab; font-size: 11px; letter-spacing: -2px; opacity: 0; user-select: none; }
.d6-r:hover .er-drag, .d6-sr:hover .er-drag, .er-cfgrow:hover .er-drag, .dx-r:hover .er-drag, .dx-lane:hover .er-drag, .er-fold:hover .er-drag { opacity: .8; }
.er-dragging { opacity: .45; }
.er-over-top { box-shadow: inset 0 2px 0 var(--io-accent); }
.er-over-bottom { box-shadow: inset 0 -2px 0 var(--io-accent); }
.er-fold { display: flex; align-items: center; justify-content: space-between; padding: 2px 4px 2px calc(var(--er-depth, 0) * 16px); }
.er-fb { display: inline-flex; align-items: center; gap: 4px; background: transparent; border: 0; padding: 3px 6px 3px 0; color: var(--io-muted); font: 600 12px var(--io-font-ui); cursor: pointer; border-radius: var(--io-radius-sm); }
.er-fb:hover { color: var(--io-text-strong); }
.er-fb[aria-expanded="true"] .sec-chev { transform: rotate(90deg); }
.er-fc { margin-left: 4px; color: var(--io-faint); font-weight: 400; font-size: 11px; }
.er-fm { display: inline-flex; margin-left: 6px; }
.er-fm .shape { width: 12px; height: 12px; }
.er-pinned { margin: 0 0 6px; padding-bottom: 4px; border-bottom: 1px solid var(--io-border); }
.er-pinh { margin: 4px 0 2px; font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: var(--io-muted); font-weight: 600; }
.er-sort { display: inline-flex; align-items: center; gap: 6px; font-size: 11.5px; color: var(--io-faint); text-transform: none; letter-spacing: 0; font-weight: 400; }
.er-sort select { font: inherit; font-size: 11.5px; color: var(--io-text); background: var(--io-field); border: 1px solid var(--io-border); border-radius: var(--io-radius-sm); padding: 1px 4px; }
.er-sortc { justify-self: end; }
`;
  function install() {
    if (typeof document === 'undefined' || document.querySelector('style[data-layout="entity-rows"]')) return;
    const style = document.createElement('style');
    style.dataset.layout = 'entity-rows';
    style.textContent = CSS;
    document.head.appendChild(style);
  }
  install();

  // ----------------------------------------------------------------------- clicks, changes, drags ----
  // Each returns true when the event was this module's. ctx = {kind, redraw}.
  function onClick(target, ctx) {
    const pin = target.closest('[data-er-pin]');
    if (pin) {
      const section = pin.closest('[data-er-section]') ? pin.closest('[data-er-section]').dataset.erSection : (pin.closest('[data-er-pinned]') || {}).dataset && pin.closest('[data-er-pinned]').dataset.erPinned;
      const sp = spec(ctx.kind, section);
      const id = pin.dataset.erPin;
      update(ctx.kind, section, { pins: sp.pins.includes(id) ? sp.pins.filter((p) => p !== id) : [...sp.pins, id] });
      ctx.redraw();
      return true;
    }
    const fold = target.closest('[data-er-fold]');
    if (fold) {
      const section = fold.closest('[data-er-section]').dataset.erSection;
      const sp = spec(ctx.kind, section);
      const path = fold.dataset.erFold;
      update(ctx.kind, section, { folded: sp.folded.includes(path) ? sp.folded.filter((p) => p !== path) : [...sp.folded, path] });
      ctx.redraw();
      return true;
    }
    return false;
  }
  function onChange(target, ctx) {
    const select = target.closest('[data-er-sort]');
    if (!select) return false;
    update(ctx.kind, select.dataset.erSort, { sort: select.value });
    ctx.redraw();
    return true;
  }
  let dragging = null;
  function onDragStart(event, ctx) {
    const handleEl = event.target instanceof Element && event.target.closest('[data-er-drag]');
    if (!handleEl) return false;
    const row = handleEl.closest('[data-er-row]');
    dragging = { section: row.dataset.erSection, parent: row.dataset.erParent, id: row.dataset.erId };
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', `${dragging.parent}\u0001${dragging.id}`);
    row.classList.add('er-dragging');
    void ctx;
    return true;
  }
  function onDragOver(event) {
    if (!dragging) return false;
    const row = event.target instanceof Element && event.target.closest('[data-er-row]');
    if (!row || row.dataset.erSection !== dragging.section || row.dataset.erParent !== dragging.parent) return false;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
    const after = event.clientY > row.getBoundingClientRect().top + row.getBoundingClientRect().height / 2;
    row.classList.toggle('er-over-top', !after);
    row.classList.toggle('er-over-bottom', after);
    return true;
  }
  function clearDrag(container) {
    container.querySelectorAll('.er-dragging, .er-over-top, .er-over-bottom').forEach((el) => el.classList.remove('er-dragging', 'er-over-top', 'er-over-bottom'));
    dragging = null;
  }
  function onDrop(event, ctx, container) {
    if (!dragging) return false;
    const row = event.target instanceof Element && event.target.closest('[data-er-row]');
    if (!row || row.dataset.erSection !== dragging.section || row.dataset.erParent !== dragging.parent) { clearDrag(container); return false; }
    event.preventDefault();
    const after = event.clientY > row.getBoundingClientRect().top + row.getBoundingClientRect().height / 2;
    const siblings = [...container.querySelectorAll('[data-er-row]')].filter((el) => el.dataset.erSection === dragging.section && el.dataset.erParent === dragging.parent).map((el) => el.dataset.erId);
    const moved = siblings.filter((id) => id !== dragging.id);
    const at = moved.indexOf(row.dataset.erId) + (after ? 1 : 0);
    moved.splice(row.dataset.erId === dragging.id ? siblings.indexOf(dragging.id) : at, 0, dragging.id);
    const { section, parent } = dragging;
    clearDrag(container);
    const sp = spec(ctx.kind, section);
    if (parent === '__pins__') update(ctx.kind, section, { pins: moved });
    else update(ctx.kind, section, { order: { ...sp.order, [parent]: moved }, sort: 'manual' });
    ctx.redraw();
    return true;
  }

  // ---------------------------------------------------------------------------- the Configuration tab ----
  const C = { query: new Map(), copied: 0, modal: null, resetOpen: new Set(), draft: new Map(), pending: new Map(), last: new Map() };
  const E = { path: null, token: null, board: '', beat: null, rendered: 0, busy: false, error: '' };
  const post = async (url, body) => {
    try {
      const response = await fetch(url, { method: 'POST', headers: PAGE_HEADERS, body: JSON.stringify(body) });
      return { status: response.status, data: await response.json() };
    } catch (error) { return { status: 0, data: { refused: 'Unreachable', message: `the host did not answer: ${error}` } }; }
  };
  const editing = (path) => E.token !== null && E.path === path;
  const refreshNode = () => { if (typeof loadNode === 'function') { try { loadNode(); } catch (_) { /* the next tick loads it */ } } };

  function stopEdit() {
    if (E.beat) clearInterval(E.beat);
    if (E.token) post('/api/v1/config/edit', { path: E.path, op: 'close', token: E.token });
    Object.assign(E, { path: null, token: null, board: '', beat: null, error: '' });
    C.pending.clear(); C.draft.clear(); C.resetOpen.clear(); C.modal = null;
  }
  async function startEdit(ctx, path) {
    const { status, data } = await post('/api/v1/config/edit', { path, op: 'open', confirm: true });
    if (!data.ok) { E.error = data.message || `HTTP ${status}`; ctx.redraw(); return; }
    Object.assign(E, { path, token: data.token, board: data.board, error: '', rendered: performance.now() });
    E.beat = setInterval(async () => {
      if (!editing(path) || performance.now() - E.rendered > STOP_AFTER_MS || (typeof document !== 'undefined' && document.hidden)) { stopEdit(); return; }
      const answer = await post('/api/v1/config/edit', { path, op: 'beat', token: E.token });
      if (!answer.data.ok) { E.error = answer.data.message || 'the edit session ended'; stopEdit(); }
    }, BEAT_MS);
    ctx.redraw();
  }

  const show = (value, unit) => `${value === null || value === undefined ? '—' : String(value)}${unit ? ` ${unit}` : ''}`;
  function coerce(row, raw) {
    if (row.type === 'int' || row.type === 'float') {
      const number = Number(String(raw).trim());
      if (String(raw).trim() === '' || !Number.isFinite(number) || (row.type === 'int' && !Number.isInteger(number))) return { error: `${raw === '' ? 'nothing' : JSON.stringify(raw)} is not a ${row.type === 'int' ? 'whole number' : 'number'}` };
      return { value: number };
    }
    if (row.type === 'bool') return { value: raw === true || raw === 'true' };
    if (row.type === 'enum') {
      const found = (row.choices || []).find((choice) => String(choice) === String(raw));
      return found === undefined ? { error: `${raw} is not one of ${(row.choices || []).join(', ')}` } : { value: found };
    }
    return { value: String(raw) };
  }

  async function apply(ctx, row, raw, confirmSafety, resetTo) {
    const parsed = resetTo ? { value: raw } : coerce(row, raw);
    if (parsed.error) { C.last.set(row.key, { state: 'refused', message: parsed.error }); ctx.redraw(); return; }
    if (row.safety && !confirmSafety) { C.modal = { kind: 'safety', key: row.key, raw, resetTo: resetTo || null, title: row.key }; ctx.redraw(); return; }
    C.pending.set(row.key, { asked: parsed.value });
    C.last.delete(row.key);
    ctx.redraw();
    const answer = resetTo
      ? await post('/api/v1/config/reset', { path: E.path, token: E.token, key: row.key, to: resetTo, confirm_safety: Boolean(confirmSafety) })
      : await post('/api/v1/config/set', { path: E.path, token: E.token, key: row.key, value: parsed.value, confirm_safety: Boolean(confirmSafety) });
    C.pending.delete(row.key);
    C.draft.delete(row.key);
    C.last.set(row.key, answer.data);
    if (answer.data.refused === 'NoEditSession') stopEdit();
    refreshNode();
    ctx.redraw();
  }
  async function saveAll(ctx) {
    E.busy = true; ctx.redraw();
    const answer = await post('/api/v1/config/persist', { path: E.path, token: E.token });
    E.busy = false;
    E.error = answer.data.ok ? '' : answer.data.message || 'the board did not save';
    if (answer.data.ok) C.last.clear();
    refreshNode(); ctx.redraw();
  }
  async function revertAll(ctx, confirmSafety) {
    E.busy = true; ctx.redraw();
    const answer = await post('/api/v1/config/reset', { path: E.path, token: E.token, to: 'stored', confirm_safety: Boolean(confirmSafety) });
    E.busy = false;
    if (answer.data.refused === 'SafetyConfirmRequired') { C.modal = { kind: 'revert-safety' }; ctx.redraw(); return; }
    E.error = answer.data.ok ? '' : answer.data.message || 'the board did not revert';
    if (answer.data.ok) { C.last.clear(); C.draft.clear(); }
    refreshNode(); ctx.redraw();
  }

  // what a row says about its edit, in plain words: the Configuration tab's and a stock diagnostic's drill-in's (one function). `mine` is this
  // page's last answer, `server` the host's own edit state (Configuration only), `unsaved` the "not saved" chip's words ('' for none)
  function chipsHtml(row, { pending, mine, server, unsaved, unsavedTitle }) {
    const parts = [];
    if (pending) return `<span class="er-chip pending" role="status">sending ${esc(show(pending.asked, row.unit))}…</span>`;
    const verdict = server && server.state !== 'pending' ? { state: server.state === 'accepted' ? 'accepted' : 'rejected', message: server.message, reset: server.reset } : mine ? { state: mine.state === 'accepted' ? 'accepted' : 'rejected', message: mine.message, reset: mine.reset } : null;
    if (verdict && verdict.state === 'accepted') parts.push(`<span class="er-chip ok" title="the board read it back and it matches what you set">✓ accepted${verdict.reset ? ` · reset to ${esc(verdict.reset)}` : ''}</span>`);
    if (verdict && verdict.state === 'rejected') parts.push(`<span class="er-chip bad" data-diagnostic role="status"><svg class="shape lv-error" role="img" aria-label="rejected"><use href="#s-error"/></svg>rejected: ${esc(verdict.message || 'the board refused it')}</span>`);
    if (unsaved) parts.push(`<span class="er-chip unsaved" title="${esc(unsavedTitle || '')}">${unsaved}</span>`);
    return parts.join(' ');
  }
  function edits(row) {
    const mine = C.last.get(row.key);
    const unsaved = row.unsaved || (mine && mine.state === 'accepted' && mine.stored !== null && mine.stored !== undefined && String(mine.value) !== String(mine.stored));
    return chipsHtml(row, { pending: C.pending.get(row.key), mine, server: row.edit, unsaved: unsaved ? `not saved · stored ${esc(show(row.unsaved ? row.stored : mine.stored, row.unit))}` : '',
      unsavedTitle: 'live in the running board; it is gone at the next restart unless you Save' });
  }
  function resetMenu(row) {
    const options = [];
    if (row.stored !== null && row.stored !== undefined && String(row.stored) !== String(row.value)) options.push(['stored', 'to the stored value', show(row.stored, row.unit)]);
    if (row.expected !== null && row.expected !== undefined && String(row.expected) !== String(row.value)) options.push(['expected', 'to the deployment record', `${show(row.expected, row.unit)}${row.expected_source ? ` · ${row.expected_source}` : ''}`]);
    if (row.default !== null && row.default !== undefined && String(row.default) !== String(row.value)) options.push(['default', 'to the factory default', show(row.default, row.unit)]);
    if (!options.length) return '<div class="er-reset muted">Nothing to reset to: it already holds its stored value, the record\'s and the default.</div>';
    return resetHtml(options, (to) => `data-er-reset="${to}" data-er-key="${esc(row.key)}"`);
  }
  // the reset buttons, `[to, label, value]` each; `attrsOf(to)` names what a click resets (shared with a stock diagnostic's drill-in)
  const resetHtml = (options, attrsOf) => `<div class="er-reset">${options.map(([to, label, value]) => `<button class="btn er-rbtn" ${attrsOf(to)}>Reset ${label}: <b>${esc(value)}</b></button>`).join('')}</div>`;
  const input = (row) => inputHtml(row, C.draft.has(row.key) ? C.draft.get(row.key) : row.value, `data-er-cfg="${esc(row.key)}"`);
  // one setting's editor by its type (a switch, a choice, a number or text), shared with a stock diagnostic's drill-in: `attrs` names the
  // field for the clicks, `placeholder` what an empty box means, `hint` the tooltip
  function inputHtml(row, draft, attrs, placeholder = '', hint = 'Enter applies it live · Esc puts back what the board holds') {
    const common = `${attrs} aria-label="${esc(row.key)}"`;
    if (row.type === 'bool') return `<label class="er-sw"><input type="checkbox" role="switch" ${common}${row.value ? ' checked' : ''}><span>${row.value ? 'true' : 'false'}</span></label>`;
    if (row.type === 'enum') return `<select class="er-in" ${common}>${(row.choices || []).map((c) => `<option value="${esc(c)}"${String(c) === String(row.value) ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>${row.unit ? `<span class="er-unit">${esc(row.unit)}</span>` : ''}`;
    const range = row.range ? ` min="${row.range[0]}" max="${row.range[1]}"` : '';
    const numeric = row.type === 'int' || row.type === 'float';
    return `<input class="er-in mono" type="${numeric ? 'number' : 'text'}"${numeric ? ` step="${row.type === 'int' ? 1 : 'any'}"${range}` : ''} ${common} value="${esc(draft)}"${placeholder ? ` placeholder="${esc(placeholder)}"` : ''} title="${esc(hint)}">${row.unit ? `<span class="er-unit">${esc(row.unit)}</span>` : ''}`;
  }

  function configRow(row, d, helpers) {
    const { h, ctx, sp } = helpers;
    const live = editing(ctx.node.path);
    const lock = row.writable ? '' : '<span class="er-lock" title="this setting is read-only">🔒</span>';
    const marks = `${star(row.key, d.pinned)}${handle(d.parent, d.id)}`;
    const differs = row.differs && !live ? `<span class="d6-diff" title="differs from what is expected">≠</span>` : '';
    const expected = row.differs ? `<div class="d6-exp">expected <b>${esc(show(row.expected, row.unit))}</b>${row.expected_source ? ` · ${esc(row.expected_source)}` : ''}</div>` : '';
    const value = live && row.writable ? `<span class="er-edit">${input(row)}</span>` : `<span class="mono d6-cv-t">${differs}${esc(show(row.value, row.unit))}</span>`;
    const resetBtn = live && row.writable ? `<button class="er-rb" data-er-resetopen="${esc(row.key)}" aria-expanded="${C.resetOpen.has(row.key)}" title="Reset this setting">↺</button>` : '';
    const safety = row.safety ? '<span class="er-safe" title="a safety setting: it asks a second confirm and is written only to a stopped board">safety</span>' : '';
    const flash = ctx.flash === row.key ? ' d6-flash' : '';
    void h; void sp;
    return `<div class="d6-cr er-cfgrow${row.differs ? ' differs' : ''}${d.pinned ? ' pinned' : ''}${flash}" ${rowAttrs(d.parent, d.id === null ? row.key : d.id, 'config')} style="--er-depth:${d.depth}">`
      + `<span class="er-name"><span class="er-ctl">${marks}</span><span class="d6-sn mono" title="${esc(row.key)}">${esc(d.label === null ? row.key : d.label)}</span>${safety}${lock}</span>`
      + `<span class="d6-cv">${value}${edits(row) ? `<div class="er-chips">${edits(row)}</div>` : ''}${expected}</span>`
      + `<span class="er-right">${resetBtn}<span class="d6-ca" title="read ${row.age_s === null ? '—' : `${h.ago(row.age_s)} ago`}">${row.age_s === null ? '' : h.ago(row.age_s)}</span></span></div>`
      + (C.resetOpen.has(row.key) && live ? `<div class="er-resetrow" style="--er-depth:${d.depth}">${resetMenu(row)}</div>` : '');
  }

  const configText = (rows) => rows.map((row) => `${row.key} = ${show(row.value, row.unit)}${row.unsaved ? `   # live; stored ${show(row.stored, row.unit)}` : ''}${row.differs ? `   # expected ${show(row.expected, row.unit)} (${row.expected_source || 'expected'})` : ''}`).join('\n');

  function configPanel(node, detail, ctx) {
    const h = ctx.h;
    E.rendered = performance.now();
    const path = node.path;
    const all = detail.config || [];
    const kind = node.kind || 'device';
    const live = editing(path);
    if (!all.length) return '<div class="muted">No configuration read from this entity. A driver sends it with its checks.</div>';
    const query = (C.query.get(path) || '').trim().toLowerCase();
    const rows = query ? all.filter((r) => `${r.key} ${show(r.value, r.unit)} ${r.expected_source}`.toLowerCase().includes(query)) : all;
    const differs = all.filter((r) => r.differs).length;
    const unsaved = all.filter((r) => r.unsaved);
    const sp = spec(kind, 'config');
    const mode = sp.sort || 'name';
    const tree = build(rows, { idOf: (r) => r.key, nameOf: (r) => r.key, sp, mode });
    const body = renderTree(tree, { section: 'config', sp, leaf: (row, d) => configRow(row, d, { h, ctx, sp }) });
    const canEdit = detail.editable;
    const editButton = live ? `<button class="btn er-edit-btn on" data-er-stop aria-pressed="true" title="Stop editing: the live values stay as they are">Editing · Stop</button>`
      : `<button class="btn er-edit-btn" data-er-start${canEdit ? '' : ' disabled'} title="${canEdit ? 'Change settings live on the board' : 'this driver does not let the page change settings'}">Edit…</button>`;
    const bar = live
      ? `<div class="er-bar${unsaved.length ? ' dirty' : ''}" role="status"><span>${unsaved.length ? `<b>${unsaved.length} change${unsaved.length === 1 ? '' : 's'} live, not saved</b>` : `Editing ${esc(E.board)} · changes apply live, the moment you press Enter`}</span>`
        + `${unsaved.length ? `<span class="er-bar-b"><button class="btn primary" data-er-save${E.busy ? ' disabled' : ''}>Save to board</button><button class="btn" data-er-revert${E.busy ? ' disabled' : ''}>Revert all</button></span>` : ''}</div>`
      : unsaved.length ? `<div class="er-bar dirty" role="status"><span><b>${unsaved.length} setting${unsaved.length === 1 ? '' : 's'} live, not saved to the board</b> · Edit… to save or revert them</span></div>` : '';
    const problem = E.error ? `<div class="er-bar bad" data-diagnostic role="alert">${esc(E.error)}</div>` : '';
    return `<div class="d6-ctl"><input type="search" class="d6-search" data-d6-search placeholder="Search ${all.length} settings" value="${esc(C.query.get(path) || '')}" aria-label="Search the configuration">`
      + `<span class="d6-dim">${rows.length === all.length ? `${all.length} settings` : `${rows.length} of ${all.length}`}${differs ? ` · ${differs} differ${differs === 1 ? 's' : ''} from what is expected` : ''}</span>`
      + `<span class="er-tools">${sortControl('config', sp, ['name', 'manual'], 'name')}${editButton}<button class="btn d6-copy" data-d6-copy>${C.copied && Date.now() - C.copied < 2000 ? 'Copied' : 'Copy as text'}</button></span></div>`
      + `${problem}${bar}<div class="d6-ch-h"><span>Setting<span class="d6-grip" data-d6-grip="name" role="separator" aria-orientation="vertical" tabindex="0" aria-label="Resize the name column" title="Drag to resize; double-click resets"></span></span><span>Value</span><span></span></div>`
      + (body || '<div class="muted">No setting matches.</div>');
  }

  function modalHtml(node, ctx) {
    const h = ctx.h;
    const m = C.modal;
    if (!m) return '';
    if (m.kind === 'start') {
      return `<div class="d6-modal" data-er-modal role="dialog" aria-modal="true" aria-label="Edit the configuration"><div class="d6-modal-c"><h3>Edit the configuration of ${h.esc(m.board)}?</h3>`
        + `<p>Each change is applied to the <b>running board</b> the moment you press Enter, and the board reads it back so you can see whether it accepted it. It is kept across a restart only when you press <b>Save to board</b>; <b>Revert all</b> puts the stored values back.</p>`
        + `<p class="muted">Safety settings (current limits, gains, limits, watchdog, ratio) ask once more, and are written only to a stopped board. Every change is logged in Activity. Editing ends when you leave this tab.</p>`
        + `<div class="d6-modal-b"><button class="btn" data-er-cancel>Cancel</button><button class="btn primary" data-er-confirm="start" autofocus>Start editing</button></div></div></div>`;
    }
    const what = m.kind === 'revert-safety' ? 'Some of the live changes are safety settings.' : `<b>${h.esc(m.title)}</b> is a safety setting.`;
    return `<div class="d6-modal" data-er-modal role="alertdialog" aria-modal="true" aria-label="Safety setting"><div class="d6-modal-c"><h3>Change a safety setting on ${h.esc(E.board)}?</h3>`
      + `<p>${what} The board must be <b>stopped</b> (mode 0) and nothing may be running on its bus; the host refuses it otherwise.</p>`
      + `<div class="d6-modal-b"><button class="btn" data-er-cancel>Cancel</button><button class="btn primary moves" data-er-confirm="${m.kind}" autofocus>Apply safety change</button></div></div></div>`;
  }

  // clicks of the Configuration tab (the page's own buttons, by data-er-*)
  function configClick(target, ctx, detail) {
    const path = ctx.node.path;
    if (target.closest('[data-er-start]')) { C.modal = { kind: 'start', board: `${ctx.node.name}${detail.badge && detail.badge.kind === 'can' ? ` · ${detail.badge.text.split(' · ')[1] || ''}` : ''}` }; ctx.redraw(); return true; }
    if (target.closest('[data-er-stop]')) { stopEdit(); ctx.redraw(); return true; }
    if (target.closest('[data-er-cancel]')) { C.modal = null; ctx.redraw(); return true; }
    const confirm = target.closest('[data-er-confirm]');
    if (confirm) {
      const m = C.modal;
      C.modal = null;
      if (m && m.kind === 'start') { startEdit(ctx, path); return true; }
      if (m && m.kind === 'safety') { const row = (detail.config || []).find((r) => r.key === m.key); if (row) apply(ctx, row, m.raw, true, m.resetTo); }
      if (m && m.kind === 'revert-safety') revertAll(ctx, true);
      ctx.redraw();
      return true;
    }
    if (target.closest('[data-er-save]')) { saveAll(ctx); return true; }
    if (target.closest('[data-er-revert]')) { revertAll(ctx, false); return true; }
    const open = target.closest('[data-er-resetopen]');
    if (open) { const key = open.dataset.erResetopen; if (C.resetOpen.has(key)) C.resetOpen.delete(key); else C.resetOpen.add(key); ctx.redraw(); return true; }
    const reset = target.closest('[data-er-reset]');
    if (reset) {
      const row = (detail.config || []).find((r) => r.key === reset.dataset.erKey);
      if (row) { C.resetOpen.delete(row.key); const to = reset.dataset.erReset; apply(ctx, row, { stored: row.stored, expected: row.expected, default: row.default }[to], false, to); }
      return true;
    }
    return false;
  }
  function configKey(event, ctx, detail) {
    const el = event.target instanceof Element && event.target.closest('[data-er-cfg]');
    if (!el || el.tagName !== 'INPUT' || el.type === 'checkbox') return false;
    const row = (detail.config || []).find((r) => r.key === el.dataset.erCfg);
    if (!row) return false;
    if (event.key === 'Enter') { event.preventDefault(); apply(ctx, row, el.value, false, null); return true; }
    if (event.key === 'Escape') { C.draft.delete(row.key); el.value = String(row.value); el.blur(); event.stopPropagation(); ctx.redraw(); return true; }
    return false;
  }
  function configInput(target, ctx) {
    if (target.matches('[data-d6-search]')) { C.query.set(ctx.node.path, target.value); ctx.redraw(); return true; }
    if (target.matches('input[data-er-cfg]') && target.type !== 'checkbox') { C.draft.set(target.dataset.erCfg, target.value); return true; }
    return false;
  }
  function configChange(target, ctx, detail) {
    const el = target.closest('[data-er-cfg]');
    if (!el || (el.tagName === 'INPUT' && el.type !== 'checkbox')) return false;
    const row = (detail.config || []).find((r) => r.key === el.dataset.erCfg);
    if (row) apply(ctx, row, el.type === 'checkbox' ? el.checked : el.value, false, null); // a switch or a choice is an explicit pick: it applies at once
    return true;
  }
  // after every draw: an input nobody is typing in shows what the board holds
  function afterDraw(container) {
    container.querySelectorAll('input[data-er-cfg]').forEach((el) => {
      if (document.activeElement !== el && el.type !== 'checkbox' && el.value !== el.getAttribute('value')) el.value = el.getAttribute('value');
    });
  }
  const copyText = (detail) => configText(detail.config || []);
  function leave() { if (E.token) stopEdit(); }

  // ------------------------------------------------------------------ a stock diagnostic's drill-in (Diagnostics tab) ----
  // A Diagnostics row whose source is robotdx.diagnostics (the host's stock diagnostics, io_diagnostics/stock_diagnostics.py) has a
  // chevron and unfolds in place: its title and doc, its config as a small form (the Configuration tab's own editing, the same
  // functions: Enter applies, each field says pending / accepted / rejected / not saved, ↺ resets it to the dataclass default), the
  // measured value against its warn / error Limits, and the window of State history it ran on. It draws GET
  // /api/v1/diagnostics/config?path=; POST /api/v1/diagnostics/config is its one write (the person's diagnostics.toml, never a device:
  // no edit session, no token). WHY (Zach, 2026-10-28): "we can actually on the diaginstics kinda like drill in and you can see and even
  // configure these values".
  const DX_SOURCE = 'robotdx.diagnostics';
  const DXC = { data: new Map(), asked: new Map(), pending: new Map(), last: new Map(), draft: new Map(), resetOpen: new Set(), all: new Set() };
  const DX_TYPES = { 'float|null': 'float', float: 'float', int: 'int', bool: 'bool', str: 'string' };
  const isStock = (check) => Boolean(check) && check.source === DX_SOURCE;
  const valueAt = (check, key) => ((check.values || []).find((v) => v.key === key) || {}).value;
  const dxKey = (path, id, field) => `${path}\u0001${id}\u0001${field}`;
  const dxRow = (field) => ({ key: field.field, value: field.value, unit: field.unit, type: DX_TYPES[field.type] || 'string', default: field.default,
    writable: Boolean(field.editable) && field.type !== 'tuple', nullable: field.type === 'float|null' });
  const dxShow = (value, unit, nullable) => (value === null || value === undefined ? (nullable ? 'no limit' : '—') : show(Array.isArray(value) ? value.join(', ') : value, unit));

  async function loadDx(path, redraw, force = false) {
    if (!force && Date.now() - (DXC.asked.get(path) || 0) < 4000) return;
    DXC.asked.set(path, Date.now());
    try {
      const response = await fetch(`/api/v1/diagnostics/config?path=${encodeURIComponent(path)}`, { signal: AbortSignal.timeout(8000) });
      if (response.ok) { DXC.data.set(path, await response.json()); redraw(); }
    } catch (_) { /* the next unfold asks again */ }
  }
  const dxField = (path, id, name) => {
    const data = DXC.data.get(path);
    const diagnostic = data && (data.diagnostics || []).find((d) => d.id === id);
    return diagnostic ? (diagnostic.fields || []).find((f) => f.field === name) : null;
  };

  async function applyDx(ctx, path, id, field, raw, reset) {
    const key = dxKey(path, id, field.field);
    const row = dxRow(field);
    const parsed = reset ? { value: field.default } : row.nullable && String(raw).trim() === '' ? { value: null } : coerce(row, raw);
    if (parsed.error) { DXC.last.set(key, { state: 'refused', message: parsed.error }); ctx.redraw(); return; }
    DXC.pending.set(key, { asked: parsed.value });
    DXC.last.delete(key);
    ctx.redraw();
    const answer = await post('/api/v1/diagnostics/config', reset ? { path, id, field: field.field, reset: true } : { path, id, field: field.field, value: parsed.value });
    DXC.pending.delete(key);
    DXC.draft.delete(key);
    DXC.last.set(key, answer.data.state === 'accepted' ? answer.data : { state: 'rejected', message: answer.data.message || answer.data.error || `HTTP ${answer.status}` });
    await loadDx(path, ctx.redraw, true);
    ctx.redraw();
  }

  // the measured value against its warn / error Limits: the headline measurement, its bounds in words and as a band
  function dxGauge(check) {
    const m = (check.measurements || [])[0];
    if (!m) return '<div class="dxc-g muted">No value: it did not run.</div>';
    const bounds = [['error_low', 'error below'], ['warn_low', 'warn below'], ['warn_high', 'warn above'], ['error_high', 'error above']]
      .filter(([k]) => typeof m[k] === 'number').map(([k, words]) => `${words} <b class="mono">${esc(show(+Number(m[k]).toPrecision(6), m.unit === '1' ? '' : m.unit))}</b>`);
    const value = typeof m.value === 'number' ? m.value : null;
    const marks = ['error_low', 'warn_low', 'warn_high', 'error_high'].map((k) => m[k]).filter((v) => typeof v === 'number');
    let band = '';
    if (value !== null && marks.length) {
      const all = [...marks, value];
      let lo = Math.min(...all); let hi = Math.max(...all);
      const pad = (hi - lo || Math.abs(hi) || 1) * 0.15; lo -= pad; hi += pad;
      const x = (v) => `${((v - lo) / (hi - lo)) * 100}%`;
      const zone = (from, to, cls) => `<span class="dxc-z ${cls}" style="left:${x(from)};width:calc(${x(to)} - ${x(from)})"></span>`;
      const zones = [];
      if (typeof m.error_low === 'number') zones.push(zone(lo, m.error_low, 'err'));
      if (typeof m.warn_low === 'number') zones.push(zone(typeof m.error_low === 'number' ? m.error_low : lo, m.warn_low, 'warn'));
      if (typeof m.warn_high === 'number') zones.push(zone(m.warn_high, typeof m.error_high === 'number' ? m.error_high : hi, 'warn'));
      if (typeof m.error_high === 'number') zones.push(zone(m.error_high, hi, 'err'));
      band = `<span class="dxc-band" data-dxc-band aria-hidden="true">${zones.join('')}<span class="dxc-v" style="left:${x(value)}"></span></span>`;
    }
    return `<div class="dxc-g"><span class="dxc-gv"><span class="dxc-k mono">${esc(m.key)}</span> <b class="mono" data-dxc-value>${esc(m.text || show(m.value, m.unit))}</b></span>`
      + `<span class="dxc-gb">${bounds.length ? bounds.join(' · ') : 'no limits: a reading, not a gate'}</span>${band}</div>`;
  }

  // the window it ran on: how much State history, which group, how many samples, how long ago it ended, how often it runs
  function dxWindow(check, ctx) {
    let window = null;
    try { window = JSON.parse(valueAt(check, 'dx.window') || 'null'); } catch (_) { window = null; }
    const every = check.expected_period_s ? ` · re-run every ${+Number(check.expected_period_s).toPrecision(3)} s` : '';
    if (!window) return `<div class="dxc-w" data-dxc-window>No window yet: ${esc(check.message || 'it has not run on any State')}${every}</div>`;
    const now = ctx.state && ctx.state.clock ? ctx.state.clock.now : null;
    const ago = now !== null ? `, ending ${ctx.h.ago(Math.max(0, now - window.to))} ago` : '';
    return `<div class="dxc-w" data-dxc-window>Ran on the last <b>${+Number(window.window_s).toPrecision(4)} s</b> of State history`
      + `${window.state ? ` (<span class="mono">${esc(window.state)}</span>)` : ''}${window.samples !== null && window.samples !== undefined ? ` · ${esc(window.samples)} samples` : ''}${ago}${every}</div>`;
  }

  function dxFieldRow(ctx, path, id, field) {
    const key = dxKey(path, id, field.field);
    const row = dxRow(field);
    const mine = DXC.last.get(key);
    const pending = DXC.pending.get(key);
    const chips = chipsHtml(row, { pending, mine, server: null, unsaved: dxUnsaved(mine), unsavedTitle: 'kept for this run only' });
    const draft = DXC.draft.has(key) ? DXC.draft.get(key) : (row.value === null || row.value === undefined ? '' : row.value);
    const value = row.writable ? `<span class="er-edit">${inputHtml(row, draft, `data-dxc-field="${esc(field.field)}" data-dxc-id="${esc(id)}"`, '', 'Enter applies it · Esc puts back the value in use')}</span>`
      : `<span class="mono d6-cv-t">${esc(dxShow(row.value, row.unit, row.nullable))}</span>`;
    const canReset = row.writable && field.source !== 'default';
    const names = `data-dxc-id="${esc(id)}" data-dxc-field="${esc(field.field)}"`;
    const resetBtn = canReset ? `<button class="er-rb" data-dxc-resetopen ${names} aria-expanded="${DXC.resetOpen.has(key)}" title="Reset this value">↺</button>` : '';
    const resetRow = canReset && DXC.resetOpen.has(key)
      ? `<div class="er-resetrow dxc-reset">${resetHtml([['default', 'to the dataclass default', dxShow(field.default, row.unit, row.nullable)]], () => `data-dxc-reset ${names}`)}</div>` : '';
    return `<div class="dxc-f" data-dxc-row="${esc(field.field)}"><span class="dxc-fn mono" title="${esc(field.doc || field.field)}">${esc(field.field)}</span>`
      + `<span class="dxc-fv">${value}${chips ? `<div class="er-chips">${chips}</div>` : ''}</span>`
      + `<span class="dxc-src ${esc(field.source)}" title="where this value comes from">${esc(field.source)}</span><span class="dxc-fr">${resetBtn}</span></div>${resetRow}`;
  }

  const PARTS = [['error_low', 'error below'], ['warn_low', 'warn below'], ['warn_high', 'warn above'], ['error_high', 'error above']];
  const SOURCE_RANK = { default: 0, toml: 1, edited: 2 };
  const PART_WORDS = Object.fromEntries(PARTS);
  // one Limits field (four `<field>.<part>` values) as one row: four boxes in the value's unit, the parts' answers under it, one ↺ menu
  function dxLimitsRow(ctx, path, id, group, parts) {
    const byPart = new Map(parts.map((f) => [f.field.split('.')[1], f]));
    const source = parts.reduce((worst, f) => (SOURCE_RANK[f.source] > SOURCE_RANK[worst] ? f.source : worst), 'default');
    const unit = (parts[0] || {}).unit || '';
    const boxes = PARTS.filter(([part]) => byPart.has(part)).map(([part, words]) => {
      const f = byPart.get(part);
      const key = dxKey(path, id, f.field);
      const row = { ...dxRow(f), unit: '' };
      const draft = DXC.draft.has(key) ? DXC.draft.get(key) : (f.value === null || f.value === undefined ? '' : f.value);
      return `<label class="dxc-part" data-dxc-part="${esc(f.field)}"><span class="dxc-pl">${words}</span>`
        + `${inputHtml(row, draft, `data-dxc-field="${esc(f.field)}" data-dxc-id="${esc(id)}"`, '—', 'Enter applies it · empty = no limit · Esc puts back the value in use')}</label>`;
    }).join('');
    const chips = parts.map((f) => {
      const key = dxKey(path, id, f.field);
      const html = chipsHtml(dxRow(f), { pending: DXC.pending.get(key), mine: DXC.last.get(key), server: null, unsaved: dxUnsaved(DXC.last.get(key)), unsavedTitle: 'kept for this run only' });
      return html ? `<span class="dxc-pc" data-dxc-chips="${esc(f.field)}"><span class="dxc-pl">${esc(PART_WORDS[f.field.split('.')[1]] || f.field)}</span> ${html}</span>` : '';
    }).join('');
    const resettable = parts.filter((f) => f.source !== 'default' && String(f.value) !== String(f.default));
    const menuKey = dxKey(path, id, group);
    const resetBtn = resettable.length ? `<button class="er-rb" data-dxc-resetopen data-dxc-id="${esc(id)}" data-dxc-field="${esc(group)}" aria-expanded="${DXC.resetOpen.has(menuKey)}" title="Reset a limit">↺</button>` : '';
    const menu = resettable.length && DXC.resetOpen.has(menuKey)
      ? `<div class="er-resetrow dxc-reset">${resetHtml(resettable.map((f) => [f.field, `${PART_WORDS[f.field.split('.')[1]] || f.field} to the dataclass default`, dxShow(f.default, unit, true)]),
        (to) => `data-dxc-reset data-dxc-id="${esc(id)}" data-dxc-field="${esc(to)}"`)}</div>` : '';
    return `<div class="dxc-f dxc-lim" data-dxc-row="${esc(group)}"><span class="dxc-fn mono" title="${esc((parts[0] || {}).doc || group)}">${esc(group)}</span>`
      + `<span class="dxc-fv"><span class="dxc-parts">${boxes}${unit && unit !== '1' ? `<span class="er-unit">${esc(unit)}</span>` : ''}</span>${chips ? `<div class="er-chips">${chips}</div>` : ''}</span>`
      + `<span class="dxc-src ${esc(source)}" title="where these values come from">${esc(source)}</span><span class="dxc-fr">${resetBtn}</span></div>${menu}`;
  }
  const dxUnsaved = (mine) => (mine && mine.state === 'accepted' && mine.saved === false ? `not saved · ${esc(mine.note || 'this host keeps no diagnostics.toml')}` : '');
  // the form: a Limits field is one row, every other field its own
  function dxForm(ctx, path, id, fields) {
    const out = [];
    const done = new Set();
    for (const f of fields) {
      if (!f.group) { out.push(dxFieldRow(ctx, path, id, f)); continue; }
      if (done.has(f.group)) continue;
      done.add(f.group);
      out.push(dxLimitsRow(ctx, path, id, f.group, fields.filter((g) => g.group === f.group)));
    }
    return out.join('');
  }
  // the library's docstrings write code as ``name``: drawn as code here
  const docHtml = (text) => esc(text).replace(/``([^`]+)``/g, '<code>$1</code>');

  // the drill-in's body: d1x's row hook (body), drawn under the row's own health detail
  function dxBody(row, ctx) {
    const check = row.check;
    if (!isStock(check)) return '';
    const path = ctx.node.path;
    const id = valueAt(check, 'dx.binding');
    loadDx(path, ctx.redraw);
    const data = DXC.data.get(path);
    const diagnostic = data && (data.diagnostics || []).find((d) => d.id === id);
    if (!diagnostic) return `<div class="dx-x dxc" data-dxc="${esc(id || '')}"><div class="muted">loading its configuration…</div></div>`;
    const metric = ((check.measurements || [])[0] || {}).key;
    const fields = diagnostic.fields || [];
    const showAll = DXC.all.has(`${path}\u0001${id}`);
    // calm by default: the headline's own Limits and every value that is not the dataclass default; the rest one click away
    const shown = showAll ? fields : fields.filter((f) => f.source !== 'default' || (f.group && f.group === metric) || f.field === 'limits' || f.group === 'limits');
    const hidden = fields.length - shown.length;
    const match = diagnostic.match || {};
    const scope = [match.part ? `every <span class="mono">${esc(match.part)}</span>` : '', match.model_id ? `model_id <span class="mono">${esc(match.model_id)}</span>` : '', match.path ? `path <span class="mono">${esc(match.path)}</span>` : ''].filter(Boolean).join(' and ');
    const doc = [diagnostic.summary, diagnostic.doc].filter(Boolean).map((text) => `<p>${docHtml(text)}</p>`).join('');
    return `<div class="dx-x dxc" data-dxc="${esc(id)}">`
      + `<div class="dxc-h"><b>${esc(diagnostic.title)}</b> <span class="mono dxc-id">${esc(diagnostic.diagnostic)} · ${esc(diagnostic.id)}</span>${diagnostic.kind === 'custom' ? ' <span class="er-chip">custom</span>' : ''}</div>`
      + `<div class="dxc-doc">${doc}</div>`
      + dxGauge(check) + dxWindow(check, ctx)
      + `<div class="dxc-form" role="group" aria-label="${esc(diagnostic.title)} configuration">${dxForm(ctx, path, id, shown)}</div>`
      + `<div class="dxc-foot">${hidden ? `<button class="btn dxc-all" data-dxc-all="${esc(id)}">Show all ${fields.length} values</button>` : showAll ? `<button class="btn dxc-all" data-dxc-all="${esc(id)}">Show fewer</button>` : ''}`
      + `<span class="muted">Enter applies a value to ${esc(data.writes || data.file || 'diagnostics.toml')}; it changes this diagnostic on every entity it matches (${scope}). The next run, within ${esc(+Number(data.beat_s).toPrecision(3))} s, uses it.</span></div></div>`;
  }
  // d1x's row hook (lead): the chevron that says a stock diagnostic unfolds
  const dxLead = (row, open) => (isStock(row.check) ? `<svg class="ic dx-chev dxc-chev${open ? ' open' : ''}" data-dxc-chev aria-hidden="true"><use href="#i-chev"/></svg>` : '');

  function dxClick(target, ctx) {
    const all = target.closest('[data-dxc-all]');
    if (all) { const k = `${ctx.node.path}\u0001${all.dataset.dxcAll}`; if (DXC.all.has(k)) DXC.all.delete(k); else DXC.all.add(k); ctx.redraw(); return true; }
    const open = target.closest('[data-dxc-resetopen]');
    if (open) { const k = dxKey(ctx.node.path, open.dataset.dxcId, open.dataset.dxcField); if (DXC.resetOpen.has(k)) DXC.resetOpen.delete(k); else DXC.resetOpen.add(k); ctx.redraw(); return true; }
    const reset = target.closest('[data-dxc-reset]');
    if (reset) {
      const field = dxField(ctx.node.path, reset.dataset.dxcId, reset.dataset.dxcField);
      if (field) {
        DXC.resetOpen.delete(dxKey(ctx.node.path, reset.dataset.dxcId, field.field));
        DXC.resetOpen.delete(dxKey(ctx.node.path, reset.dataset.dxcId, field.group || field.field));
        applyDx(ctx, ctx.node.path, reset.dataset.dxcId, field, null, true);
      }
      return true;
    }
    return Boolean(target.closest('.dxc')); // a click inside the drill-in never folds the row
  }
  function dxKeyDown(event, ctx) {
    const el = event.target instanceof Element && event.target.closest('[data-dxc-field]');
    if (!el || el.tagName !== 'INPUT' || el.type === 'checkbox') return false;
    const field = dxField(ctx.node.path, el.dataset.dxcId, el.dataset.dxcField);
    if (!field) return false;
    const key = dxKey(ctx.node.path, el.dataset.dxcId, field.field);
    if (event.key === 'Enter') { event.preventDefault(); applyDx(ctx, ctx.node.path, el.dataset.dxcId, field, el.value, false); return true; }
    if (event.key === 'Escape') { DXC.draft.delete(key); el.value = field.value === null || field.value === undefined ? '' : String(field.value); el.blur(); event.stopPropagation(); ctx.redraw(); return true; }
    return false;
  }
  function dxInput(target, ctx) {
    if (!target.matches('input[data-dxc-field]') || target.type === 'checkbox') return false;
    DXC.draft.set(dxKey(ctx.node.path, target.dataset.dxcId, target.dataset.dxcField), target.value);
    return true;
  }
  function dxChange(target, ctx) {
    const el = target.closest('[data-dxc-field]');
    if (!el || (el.tagName === 'INPUT' && el.type !== 'checkbox')) return false;
    const field = dxField(ctx.node.path, el.dataset.dxcId, el.dataset.dxcField);
    if (field) applyDx(ctx, ctx.node.path, el.dataset.dxcId, field, el.type === 'checkbox' ? el.checked : el.value, false); // a switch is an explicit pick: it applies at once
    return true;
  }
  function dxAfterDraw(container) {
    container.querySelectorAll('input[data-dxc-field]').forEach((el) => {
      if (document.activeElement !== el && el.type !== 'checkbox' && el.value !== el.getAttribute('value')) el.value = el.getAttribute('value');
    });
  }

  root.IOEntityRows = {
    loadArrangements, spec, update, build, renderTree, sortControl, star, handle, rowAttrs, natural, RANK, folderPath, countLeaves, install,
    onClick, onChange, onDragStart, onDragOver, onDrop, clearDrag,
    config: { panel: configPanel, modalHtml, click: configClick, key: configKey, input: configInput, change: configChange, afterDraw, copyText, leave, editing,
      state: { C, E }, stop: stopEdit },
    dx: { lead: dxLead, body: dxBody, click: dxClick, key: dxKeyDown, input: dxInput, change: dxChange, afterDraw: dxAfterDraw, isStock, state: DXC },
    arrangement: A,
  };
})(typeof window !== 'undefined' ? window : globalThis);
