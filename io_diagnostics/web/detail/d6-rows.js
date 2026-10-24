// window.IOEntityRows: the entity page's shared ROW MODEL (docs/state-config-v1.md section 10) and the Configuration tab's editing
// (section 9). Loaded by index.html before the layouts; d6.js draws with it, nothing else does.
//
// ONE row model for Diagnostics, State and Configuration (Zach, 2026-10-24, "# COnfiguration page"):
//  - a row named in dot notation (servo.max_current_A, motor_position.rotor_to_output_ratio, arm.servo.current) is a leaf in a
//    folder tree, like tview's config tree; a name with spaces, a colon or no dot stays at the top level;
//  - order inside a folder: alphabetical (folders first), or MANUAL (drag a row by its handle), and starred rows first as a
//    "Pinned" block; Diagnostics may also sort by severity (its default);
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
      const response = await fetch('/api/v1/arrangements', { cache: 'no-store' });
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

  // items -> {pinned: [item], root: folder}; folder = {type: 'folder', name, path, children: [folder | leaf]}, leaf = {type: 'leaf', name, item}
  function build(items, { idOf, nameOf, sp, mode, severityOf }) {
    const pinned = sp.pins.map((id) => items.find((item) => idOf(item) === id)).filter(Boolean);
    const pinnedIds = new Set(pinned.map(idOf));
    const rest = items.filter((item) => !pinnedIds.has(idOf(item)));
    const root = { type: 'folder', name: '', path: '', children: [] };
    for (const item of rest) {
      const parts = segments(nameOf(item));
      let folder = root;
      parts.slice(0, -1).forEach((part, at) => {
        let next = folder.children.find((child) => child.type === 'folder' && child.name === part);
        if (!next) { next = { type: 'folder', name: part, path: parts.slice(0, at + 1).join('.'), children: [] }; folder.children.push(next); }
        folder = next;
      });
      folder.children.push({ type: 'leaf', name: parts[parts.length - 1], item });
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
    return { pinned, root };
  }
  const countLeaves = (folder) => folder.children.reduce((n, child) => n + (child.type === 'leaf' ? 1 : countLeaves(child)), 0);

  // The page's own marks on a row: its star (pin) and its drag handle. `id` is what the arrangement stores.
  const star = (id, on) => `<button class="er-pin${on ? ' on' : ''}" data-er-pin="${esc(id)}" aria-pressed="${on}" title="${on ? 'Unpin' : 'Pin to the top'}" aria-label="${on ? 'Unpin' : 'Pin'} ${esc(id)}">${on ? '★' : '☆'}</button>`;
  const handle = (parent, name) => `<span class="er-drag" draggable="true" data-er-drag data-er-parent="${esc(parent)}" data-er-id="${esc(name)}" title="Drag to reorder" aria-hidden="true">⋮⋮</span>`;
  const rowAttrs = (parent, name, section) => `data-er-row data-er-parent="${esc(parent)}" data-er-id="${esc(name)}" data-er-section="${section}"`;

  // renders the pinned block and the tree. leaf(item, {label, depth, parent, id, pinned}) draws one row (its own markup, with the
  // marks from star() / handle() and rowAttrs()); the folders are drawn here.
  function renderTree(result, { section, sp, leaf }) {
    const folded = new Set(sp.folded);
    const out = [];
    if (result.pinned.length) {
      out.push(`<div class="er-pinned" data-er-pinned="${section}"><h4 class="er-pinh">★ Pinned</h4>${result.pinned.map((item) => leaf(item, { label: null, depth: 0, parent: '__pins__', id: null, pinned: true })).join('')}</div>`);
    }
    const walk = (folder, depth) => {
      for (const child of folder.children) {
        if (child.type === 'leaf') { out.push(leaf(child.item, { label: child.name, depth, parent: folder.path, id: child.name, pinned: false })); continue; }
        // a chain of folders that each hold one folder (aux1 > pins > 0) is one row: aux1.pins.0
        let node = child;
        let label = child.name;
        while (node.children.length === 1 && node.children[0].type === 'folder') { node = node.children[0]; label += `.${node.name}`; }
        const open = !folded.has(node.path);
        out.push(`<div class="er-fold" ${rowAttrs(folder.path, child.name, section)} style="--er-depth:${depth}"><button class="er-fb" data-er-fold="${esc(node.path)}" aria-expanded="${open}">`
          + `<svg class="ic sec-chev"><use href="#i-chev"/></svg><span class="er-fn">${esc(label)}</span><span class="er-fc">${countLeaves(node)}</span></button>${handle(folder.path, child.name)}</div>`);
        if (open) walk(node, depth + 1);
      }
    };
    walk(result.root, 0);
    return out.join('');
  }

  // the little order switch every tab carries: A–Z · Manual (· Severity for Diagnostics), and where the arrangement lives
  function sortControl(section, sp, modes, defaultMode) {
    const mode = sp.sort || defaultMode;
    const where = A.local ? 'kept in this browser: the host keeps no arrangements' : A.file ? `kept in ${A.file} (every ${'board of this kind'} shares it)` : 'kept for this run only (the host has no config file)';
    return `<label class="er-sort" title="${esc(where)}"><span>Order</span><select data-er-sort="${section}" aria-label="Order of the rows">${modes.map((m) => `<option value="${m}"${m === mode ? ' selected' : ''}>${SORTS[m]}</option>`).join('')}</select></label>`;
  }

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

  // what a row says about its edit, in plain words
  function edits(row) {
    const parts = [];
    const pending = C.pending.get(row.key);
    const mine = C.last.get(row.key);
    const server = row.edit;
    if (pending) return `<span class="er-chip pending" role="status">sending ${esc(show(pending.asked, row.unit))}…</span>`;
    const verdict = server && server.state !== 'pending' ? { state: server.state === 'accepted' ? 'accepted' : 'rejected', message: server.message, reset: server.reset } : mine ? { state: mine.state === 'accepted' ? 'accepted' : 'rejected', message: mine.message, reset: mine.reset } : null;
    if (verdict && verdict.state === 'accepted') parts.push(`<span class="er-chip ok" title="the board read it back and it matches what you set">✓ accepted${verdict.reset ? ` · reset to ${esc(verdict.reset)}` : ''}</span>`);
    if (verdict && verdict.state === 'rejected') parts.push(`<span class="er-chip bad" data-diagnostic role="status"><svg class="shape lv-error" role="img" aria-label="rejected"><use href="#s-error"/></svg>rejected: ${esc(verdict.message || 'the board refused it')}</span>`);
    const unsaved = row.unsaved || (mine && mine.state === 'accepted' && mine.stored !== null && mine.stored !== undefined && String(mine.value) !== String(mine.stored));
    if (unsaved) parts.push(`<span class="er-chip unsaved" title="live in the running board; it is gone at the next restart unless you Save">not saved · stored ${esc(show(row.unsaved ? row.stored : mine.stored, row.unit))}</span>`);
    return parts.join(' ');
  }
  function resetMenu(row) {
    const options = [];
    if (row.stored !== null && row.stored !== undefined && String(row.stored) !== String(row.value)) options.push(['stored', 'to the stored value', show(row.stored, row.unit)]);
    if (row.expected !== null && row.expected !== undefined && String(row.expected) !== String(row.value)) options.push(['expected', 'to the deployment record', `${show(row.expected, row.unit)}${row.expected_source ? ` · ${row.expected_source}` : ''}`]);
    if (row.default !== null && row.default !== undefined && String(row.default) !== String(row.value)) options.push(['default', 'to the factory default', show(row.default, row.unit)]);
    if (!options.length) return '<div class="er-reset muted">Nothing to reset to: it already holds its stored value, the record\'s and the default.</div>';
    return `<div class="er-reset">${options.map(([to, label, value]) => `<button class="btn er-rbtn" data-er-reset="${to}" data-er-key="${esc(row.key)}">Reset ${label}: <b>${esc(value)}</b></button>`).join('')}</div>`;
  }
  function input(row) {
    const draft = C.draft.has(row.key) ? C.draft.get(row.key) : row.value;
    const common = `data-er-cfg="${esc(row.key)}" aria-label="${esc(row.key)}"`;
    if (row.type === 'bool') return `<label class="er-sw"><input type="checkbox" role="switch" ${common}${row.value ? ' checked' : ''}><span>${row.value ? 'true' : 'false'}</span></label>`;
    if (row.type === 'enum') return `<select class="er-in" ${common}>${(row.choices || []).map((c) => `<option value="${esc(c)}"${String(c) === String(row.value) ? ' selected' : ''}>${esc(c)}</option>`).join('')}</select>${row.unit ? `<span class="er-unit">${esc(row.unit)}</span>` : ''}`;
    const range = row.range ? ` min="${row.range[0]}" max="${row.range[1]}"` : '';
    const numeric = row.type === 'int' || row.type === 'float';
    return `<input class="er-in mono" type="${numeric ? 'number' : 'text'}"${numeric ? ` step="${row.type === 'int' ? 1 : 'any'}"${range}` : ''} ${common} value="${esc(draft)}" title="Enter applies it live · Esc puts back what the board holds">${row.unit ? `<span class="er-unit">${esc(row.unit)}</span>` : ''}`;
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

  root.IOEntityRows = {
    loadArrangements, spec, update, build, renderTree, sortControl, star, handle, rowAttrs, natural, RANK,
    onClick, onChange, onDragStart, onDragOver, onDrop, clearDrag,
    config: { panel: configPanel, modalHtml, click: configClick, key: configKey, input: configInput, change: configChange, afterDraw, copyText, leave, editing,
      state: { C, E }, stop: stopEdit },
    arrangement: A,
  };
})(typeof window !== 'undefined' ? window : globalThis);
