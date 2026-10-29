/* Dashboards: the starters (dashboards/*.base, listed in dashboards/index.json) and your own (docs/peps/0001-robotdx-on-data-ui.md
   §12.3, §14).

   A dashboard is a data-ui .base with layout: (PEP 0004 / 0005 grammar), drawn by a stock <data-dashboard> over the page's
   live datasets (app.js openBase). Yours are files on the host (GET /api/v1/dashboards, GET | PUT | DELETE
   /api/v1/dashboards/<name>.base; the host keeps the text byte for byte and never parses it). Every change is one PUT of
   the whole text with If-Match, so two tabs never write over each other: a 412 is shown as a conflict, never retried
   (Add to dashboard, add.js, appends its panel to their text once: it only inserts lines).
   On an older host (404) or one with no config dir (503) they stay in this browser (localStorage robotdx.dx.boards), and
   a dashboard left there moves to the host on its first save there. Save as file and Load .base (picker, drop) always work.
   WHY one store interface ({list, read, write, remove, create}) with two backends: the host's store slots in where the
   browser's was, and nothing above it changes (PEP §12.3's reversible default, now taken).

   Make one: New (an empty dashboard with the time bar), Add panel (a plot, levels, KPI cards or a table of one entity,
   or the playback card), Edit .base (the text itself), Arrange (data-ui's: drag and resize; the arrangement is written
   back into your dashboard's text, so it travels with Save as file). A starter is read only: Duplicate makes it yours. */

import { esc, slug } from '../vendor/data-ui/data-ui.js';

const KEY = 'robotdx.dx.boards';
const API = '/api/v1/dashboards';
const NAME = /^[A-Za-z0-9][A-Za-z0-9 _.-]{0,79}$/;   // the host's rule (docs/api-v1.md "Dashboards as files")
const NEW_TEXT = `# A dashboard of your own (RobotDx on data-ui). Add panel puts views here, Edit .base changes this text, and
# Arrange drags and resizes the panels. Save as file gives you the .base to keep or share.
datasets:
  summary:
    data: "../datasets/summary.json"
clock: { rate: 1 }
layout:
  columns: 12
  rowHeight: 96
  panels:
    - { view: Time, w: 12, h: 1 }
views:
  - type: playback
    name: Time
    from: summary
`;
const LEVELS = { state: 'level', label: 'message', order: ['ok', 'warn', 'unknown', 'stale', 'error'], tones: { ok: 'good', warn: 'warn', unknown: 'muted', stale: 'muted', error: 'bad' }, bar: 3 };

/** A title as a name the host takes: other characters become a dash; never empty, never a starter's id. */
export function nameFor(title, taken = new Set()) {
  let base = String(title || 'Untitled dashboard').replace(/[^A-Za-z0-9 _.-]+/g, '-').replace(/-{2,}/g, '-').replace(/^[^A-Za-z0-9]+/, '').trim().slice(0, 72) || 'Dashboard';
  if (!NAME.test(base)) base = 'Dashboard';
  let name = base;
  for (let n = 2; taken.has(name.toLowerCase()); n += 1) name = `${base} ${n}`;
  return name;
}

// ---- this browser's store (phase 3; an older host's) --------------------------------------------------------------------------
export class LocalStore {
  get kind() { return 'local'; }
  all() {
    try { const v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v.filter((b) => b && b.id && typeof b.text === 'string') : []; } catch (_) { return this._memory || []; }
  }
  _put(list) {
    this._memory = list;
    try { if (list.length) localStorage.setItem(KEY, JSON.stringify(list)); else localStorage.removeItem(KEY); } catch (_) { /* private window: this visit only */ }
  }
  async list() { return this.all().map((b) => ({ id: b.id, title: b.title, updated: b.updated })); }
  async read(id) { const b = this.all().find((x) => x.id === id); return b ? { text: b.text, title: b.title, etag: null } : null; }
  async write(id, text) {
    const list = this.all();
    const b = list.find((x) => x.id === id);
    this._put([...list.filter((x) => x.id !== id), { ...(b || { id, title: id }), text, updated: new Date().toISOString() }]);
    return { ok: true, id, etag: null };
  }
  async remove(id) { this._put(this.all().filter((b) => b.id !== id)); return { ok: true }; }
  async create({ title, text }) {
    let id = `my-${slug(title || 'dashboard') || 'dashboard'}`;
    const taken = new Set(this.all().map((b) => b.id));
    for (let n = 2; taken.has(id); n += 1) id = `my-${slug(title || 'dashboard')}-${n}`;
    this._put([...this.all(), { id, title: title || 'Untitled dashboard', text, updated: new Date().toISOString() }]);
    return id;
  }
}

// ---- the host's store: files under its config dir -------------------------------------------------------------------------------
const fileUrl = (name) => `${API}/${encodeURIComponent(name)}.base`;
// WHY these headers: a write is the page's own (server.py _from_page: its header; the browser adds Sec-Fetch-Site and Origin)
const WRITE_HEADERS = { 'X-IO-Diagnostics': 'ui', 'Content-Type': 'text/plain; charset=utf-8' };
const quoted = (etag) => (etag ? `"${String(etag).replace(/^"|"$/g, '')}"` : null);

export class HostStore {
  constructor(starterIds = []) { this.starters = new Set(starterIds.map((s) => s.toLowerCase())); this.files = []; }
  get kind() { return 'host'; }
  async list() {
    const res = await fetch(API, { cache: 'no-store' });
    if (!res.ok) throw new Error(`${res.status} ${API}`);
    const body = await res.json();
    this.files = Array.isArray(body.files) ? body.files : [];
    return this.files.map((f) => ({ id: f.name, title: f.name, updated: new Date(f.mtime * 1000).toISOString(), etag: f.etag, size: f.size }));
  }
  taken() { return new Set([...this.starters, ...this.files.map((f) => f.name.toLowerCase())]); }
  async read(name) {
    const res = await fetch(fileUrl(name), { cache: 'no-store' });
    if (res.status === 404) return null;
    if (!res.ok) throw new Error(`${res.status} reading ${name}.base`);
    return { text: await res.text(), title: name, etag: (res.headers.get('ETag') || '').replace(/^"|"$/g, '') || null };
  }
  /** One PUT of the whole text; `etag` null for a new file, `create` for a file that must not be there yet (If-None-Match: *).
      {ok, etag} | {conflict, etag} | {tooLarge} | {error}. */
  async write(name, text, etag, { create = false } = {}) {
    const headers = { ...WRITE_HEADERS };
    if (etag) headers['If-Match'] = quoted(etag);
    // WHY (PEP 0001 §17): a name another tab took between the list and this write is refused (412 Exists), never written over
    if (create) headers['If-None-Match'] = '*';
    let res;
    try { res = await fetch(fileUrl(name), { method: 'PUT', headers, body: text, cache: 'no-store' }); } catch (err) { return { error: `the host did not answer (${err.message})` }; }
    const body = await res.json().catch(() => ({}));
    if (res.ok) return { ok: true, id: name, etag: body.etag || (res.headers.get('ETag') || '').replace(/^"|"$/g, '') };
    if (res.status === 412) return { conflict: true, etag: body.etag ?? null };
    if (res.status === 413) return { tooLarge: true };
    return { error: body.error || `${res.status}` };
  }
  async remove(name, etag) {
    const headers = { 'X-IO-Diagnostics': 'ui' };
    if (etag) headers['If-Match'] = quoted(etag);
    const res = await fetch(fileUrl(name), { method: 'DELETE', headers, cache: 'no-store' });
    const body = await res.json().catch(() => ({}));
    if (res.ok) return { ok: true, trashed: body.trashed };
    if (res.status === 412) return { conflict: true };
    return { error: body.error || `${res.status}` };
  }
  /** A new file under a free name from `title`; returns its name, or throws. */
  async create({ title, text }) {
    // WHY created only (If-None-Match: *) and tried again: two tabs pressing New at once both found "Untitled dashboard" free, and
    // the second write replaced the first; now the second is refused and takes the next free name
    for (let tries = 0; tries < 3; tries += 1) {
      await this.list().catch(() => {});
      const name = nameFor(title, this.taken());
      const r = await this.write(name, text, null, { create: true });
      if (r.ok) { this.files.push({ name, etag: r.etag, mtime: Date.now() / 1000 }); return name; }
      if (!r.conflict) throw new Error(r.tooLarge ? 'over the host\'s 1 MB' : r.error || 'the host refused it');
    }
    throw new Error('another tab kept taking the same name; try again');
  }
}

/** The host's store when it answers GET /api/v1/dashboards with {files}; this browser's when it 404s (older) or 503s (no config dir). */
export async function storeFor(starterIds) {
  try {
    const res = await fetch(API, { cache: 'no-store' });
    if (res.ok) {
      const body = await res.json();
      if (body && Array.isArray(body.files)) { const host = new HostStore(starterIds); host.files = body.files; return host; }
    }
  } catch (_) { /* below */ }
  return new LocalStore();
}

const local = new LocalStore();
let store = null;   // the one the page uses, found on first need
async function theStore(BOARDS) {
  if (!store) store = await storeFor((await starters(BOARDS)).map((s) => s.id));
  return store;
}
/** The page's one dashboard store (the host's files, or this browser's on an older host): Add to dashboard saves through it (add.js). */
export const storeOf = theStore;
// Explore's "Keep as a dashboard" and the checks reach the store here
window.__dxBoards = { create: async (b) => (await theStore(new URL('dashboards/', import.meta.url).href)).create(b), kind: () => (store ? store.kind : null) };

async function starters(BOARDS) {
  try { return await (await fetch(new URL('index.json', BOARDS), { cache: 'no-store' })).json(); } catch (_) { return []; }
}

/** Read .base files (picked or dropped) into your dashboards; returns how many went in, or throws the first error. */
async function loadFiles(files, parseBase, s) {
  let n = 0;
  for (const file of files) {
    const text = await file.text();
    parseBase(text);   // WHY first: a file that does not parse is refused with its error, never stored half-read
    await s.create({ title: file.name.replace(/\.base$/i, ''), text });
    n += 1;
  }
  return n;
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
  const a = Object.assign(document.createElement('a'), { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function mountDashboards(root, params, { openBase, go, showTimeBar, BOARDS, feed }) {
  const { parseBase, stringifyBase } = window.robotdx;
  const list = await starters(BOARDS);
  const s = await theStore(BOARDS);
  // a .base dropped anywhere on the destination becomes one of yours (the list redraws to show it)
  const onDrag = (e) => { if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); root.classList.add('dx-drop'); } };
  const onLeave = () => root.classList.remove('dx-drop');
  const onDrop = async (e) => {
    const files = [...(e.dataTransfer?.files || [])].filter((f) => /\.base$/i.test(f.name) || f.type.startsWith('text/'));
    if (!files.length) return;
    e.preventDefault();
    root.classList.remove('dx-drop');
    try { await loadFiles(files, parseBase, s); } catch (err) { alertIn(root, `That file is not a .base this page can keep: ${err.message}`); return; }
    if (params.board) go('dashboards'); else drawList();
  };
  root.addEventListener('dragenter', onDrag);
  root.addEventListener('dragover', onDrag);
  root.addEventListener('dragleave', onLeave);
  root.addEventListener('drop', onDrop);
  const off = () => { root.removeEventListener('dragenter', onDrag); root.removeEventListener('dragover', onDrag); root.removeEventListener('dragleave', onLeave); root.removeEventListener('drop', onDrop); };

  async function drawList() {
    let mine = [];
    let note = '';
    try { mine = await s.list(); } catch (err) { note = `The host's dashboards could not be read (${esc(err.message)}).`; }
    // drafts: dashboards this browser kept before the host had a place for them (phase 3); each moves on its first save
    const drafts = s.kind === 'host' ? local.all() : [];
    const where = s.kind === 'host' ? 'on the host' : 'in this browser';
    root.innerHTML = `<div class="dx-boards">
      <div class="dx-bar"><h1>Dashboards</h1><span class="dx-note">yours are kept ${where}</span><span class="dx-grow"></span>
        <button type="button" class="dx-btn dx-primary" data-board-act="new">New dashboard</button>
        <button type="button" class="dx-btn" data-board-act="load">Load .base…</button>
        <input type="file" id="dx-load-input" accept=".base,text/plain" multiple hidden>
      </div>
      <section><h2>Starters</h2><ul class="dx-board-list">${list.map((st) => `<li><a href="?go=dashboards&board=${encodeURIComponent(st.id)}" data-board-id="${esc(st.id)}" data-board-kind="starter"><b>${esc(st.title)}</b><small>${esc(st.about || st.file)}</small></a></li>`).join('')}</ul></section>
      <section><h2>Yours</h2>${note ? `<p class="dx-error">${note}</p>` : ''}${mine.length || drafts.length ? `<ul class="dx-board-list">${mine.map((b) => `<li><a href="?go=dashboards&board=${encodeURIComponent(b.id)}" data-board-id="${esc(b.id)}" data-board-kind="mine"><b>${esc(b.title)}</b><small>${esc(where)} · ${esc(String(b.updated || '').replace('T', ' ').slice(0, 16))}</small></a></li>`).join('')}${drafts.map((b) => `<li><a href="?go=dashboards&board=${encodeURIComponent(`draft:${b.id}`)}" data-board-id="${esc(`draft:${b.id}`)}" data-board-kind="draft"><b>${esc(b.title)}</b><small>in this browser · moves to the host on its first save</small></a></li>`).join('')}</ul>`
        : '<p class="dx-empty" style="padding:0">None yet: New dashboard, or Load .base (or drop a .base file on this page).</p>'}</section>
    </div>`;
    root.querySelector('[data-board-act="new"]').addEventListener('click', async () => {
      try { go('dashboards', { board: await s.create({ title: 'Untitled dashboard', text: NEW_TEXT }) }); } catch (err) { alertIn(root, `No new dashboard: ${err.message}`); }
    });
    const input = root.querySelector('#dx-load-input');
    root.querySelector('[data-board-act="load"]').addEventListener('click', () => input.click());
    input.addEventListener('change', async () => {
      try { await loadFiles([...input.files], parseBase, s); } catch (err) { alertIn(root, `That file is not a .base this page can keep: ${err.message}`); return; }
      drawList();
    });
    for (const a of root.querySelectorAll('[data-board-id]')) {
      a.addEventListener('click', (e) => { if (e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); go('dashboards', { board: a.dataset.boardId }); } });
    }
    showTimeBar(true);
  }

  if (!params.board) {
    await drawList();
    return { update: (p) => (p.board ? false : (drawList(), null)), unmount: off };
  }

  // ---- one dashboard ------------------------------------------------------------------------------------------------------
  const starter = list.find((st) => st.id === params.board);
  const draftId = params.board.startsWith('draft:') ? params.board.slice(6) : null;
  let own = null;      // {id, title, text, etag, draft}
  if (!starter) {
    try {
      if (draftId) { const d = await local.read(draftId); if (d) own = { id: params.board, title: d.title, text: d.text, etag: null, draft: draftId }; }
      else { const r = await s.read(params.board); if (r) own = { id: params.board, title: r.title, text: r.text, etag: r.etag, draft: null }; }
    } catch (err) {
      root.innerHTML = `<p class="dx-empty dx-error">This dashboard could not be read: ${esc(err.message)}. <a class="dx-link" href="?go=dashboards">All dashboards</a></p>`;
      return { update: () => false, unmount: off };
    }
  }
  if (!starter && !own) {
    root.innerHTML = `<p class="dx-empty">No dashboard “${esc(params.board)}” here. <a class="dx-link" href="?go=dashboards">All dashboards</a></p>`;
    return { update: () => false, unmount: off };
  }
  const title = starter ? starter.title : own.title;
  let text = starter ? await (await fetch(new URL(starter.file, BOARDS), { cache: 'no-store' })).text() : own.text;
  const kindNote = starter ? `starter · ${esc(starter.file)}` : own.draft ? 'in this browser · moves to the host on its first save' : s.kind === 'host' ? `yours · on the host (${esc(params.board)}.base)` : 'yours · in this browser';
  root.innerHTML = `<div class="dx-board-view">
    <div class="dx-bar"><a class="dx-link" href="?go=dashboards" data-back>‹ Dashboards</a><h1>${esc(title)}</h1>
      <span class="dx-note" id="dx-board-note">${kindNote}</span><span class="dx-grow"></span>
      ${starter ? '<button type="button" class="dx-btn" data-board-act="duplicate" title="Make a copy of your own to change">Duplicate</button>'
        : '<button type="button" class="dx-btn" data-board-act="add">Add panel</button><button type="button" class="dx-btn" data-board-act="edit">Edit .base</button>'}
      <button type="button" class="dx-btn" data-board-act="save" title="Download this dashboard as a .base file (the arrangement is its layout:)">Save as file</button>
      <button type="button" class="dx-btn" data-board-act="copy" title="Put the .base text on the clipboard">Copy .base</button>
      ${own ? '<button type="button" class="dx-btn" data-board-act="delete" title="Delete this dashboard (the host keeps it in its .trash/ folder)">Delete</button>' : ''}
    </div>
    <div class="dx-conflict" data-board-conflict role="alert" hidden></div>
    <data-dashboard class="dx-board" id="dx-board-${esc(slug(params.board) || 'board')}"></data-dashboard>
    <p class="dx-error" id="dx-board-error" hidden></p>
  </div>`;
  root.querySelector('[data-back]').addEventListener('click', (e) => { e.preventDefault(); go('dashboards'); });
  const el = root.querySelector('data-dashboard');
  const errorBox = root.querySelector('#dx-board-error');
  const conflictBox = root.querySelector('[data-board-conflict]');
  let opened = null;
  async function draw(t) {
    let next;
    try { next = await openBase(el, t, BOARDS, { keepState: !!opened }); } catch (err) {
      errorBox.hidden = false;
      errorBox.textContent = `This .base does not draw: ${err.message}`;
      return false;
    }
    errorBox.hidden = true;
    if (opened) opened.release();
    opened = next;
    showTimeBar(!(opened.base.views || []).some((v) => v.type === 'playback'));
    return true;
  }
  await draw(text);
  // Add to dashboard (add.js) saved THIS dashboard: its text and etag are the ones written (so this page's next save of it is not
  // refused as somebody else's), and it is drawn again with the new panel at the bottom (data-ui reloads nothing: the host does,
  // CONTRACT §34). WHY not at once when the add came from one of this dashboard's own panels: drawing it again replaces that
  // panel, and the picker inside it, before the picker can say "Added to …"; it says so, and the dashboard is drawn again when the
  // reader closes it, or 2 s later (asked of data-ui: keep the picker across a reload of the dashboard it came from)
  let pending = null;   // the redraw waiting for the picker: cancelled when this dashboard leaves the page
  const onSaved = (e) => {
    if (!own || own.draft || e.detail.id !== own.id) return;
    own.etag = e.detail.etag;
    text = e.detail.text;
    conflictBox.hidden = true;
    if (pending) pending.cancel();
    const pick = el.querySelector('.dv-addpick');
    if (!pick) { draw(text); return; }
    const next = text;
    let timer = null;
    const watch = new MutationObserver(() => { if (!pick.isConnected) now(); });
    const cancel = () => { clearTimeout(timer); watch.disconnect(); if (pending && pending.cancel === cancel) pending = null; };
    function now() {
      if (!pending || pending.cancel !== cancel) return;
      cancel();
      const close = pick.isConnected && pick.querySelector('.dv-addpick-x');
      if (close) close.click();
      if (text === next) draw(next);
    }
    pending = { cancel };
    if (pick.parentNode) watch.observe(pick.parentNode, { childList: true });
    timer = setTimeout(now, 2000);
  };
  addEventListener('dx-board-saved', onSaved);

  /** Keep `next` as this dashboard's text: a draft's first save moves it to the host; a host file is one PUT with If-Match. */
  async function keep(next) {
    if (!own) return false;
    if (own.draft && s.kind === 'host') {
      try {
        const name = await s.create({ title: own.title, text: next });
        await local.remove(own.draft);
        text = next;
        go('dashboards', { board: name }, { replace: true });
        return true;
      } catch (err) { showProblem(`Not saved to the host: ${err.message}`); return false; }
    }
    const r = await (own.draft ? local.write(own.draft, next) : s.write(own.id, next, own.etag));
    if (r.ok) { text = next; own.etag = r.etag; conflictBox.hidden = true; return true; }
    if (r.conflict) {
      // WHY never retried: another tab or person saved since this page read it; theirs stays, and the choice is the reader's
      conflictBox.hidden = false;
      conflictBox.innerHTML = `<b>Not saved: this dashboard changed since you opened it</b> (another tab or person saved it). Your change is not on the host.
        <button type="button" class="dx-btn" data-conflict="reload">Reload theirs</button><button type="button" class="dx-btn" data-conflict="copy">Save mine as a copy</button>`;
      conflictBox.querySelector('[data-conflict="reload"]').addEventListener('click', () => location.reload());
      conflictBox.querySelector('[data-conflict="copy"]').addEventListener('click', async () => {
        try { go('dashboards', { board: await s.create({ title: `${own.title} copy`, text: next }) }); } catch (err) { showProblem(`No copy: ${err.message}`); }
      });
      return false;
    }
    if (r.tooLarge) { showProblem('Not saved: the text is over the host\'s 1 MB. Save as file keeps it.'); return false; }
    showProblem(`Not saved: ${r.error}`);
    return false;
  }
  function showProblem(message) { conflictBox.hidden = false; conflictBox.textContent = message; }

  // Arrange (data-ui's) writes your arrangement back into your text, so Save as file carries it
  el.addEventListener('data-dashboard-layout', async (e) => {
    if (!own) return;
    if (await keep(e.detail.text)) {
      // WHY forget data-ui's own copy of the arrangement: the text holds it now, and a later Edit .base must win
      try { for (const k of Object.keys(localStorage)) if (k.startsWith('data-ui/dash-layout:') && k.endsWith(`/${el.id}`)) localStorage.removeItem(k); } catch (_) { /* nothing stored */ }
    }
  });
  const textNow = () => (own ? text : (el.layoutText ? el.layoutText() : text));
  root.querySelector('[data-board-act="save"]').addEventListener('click', () => download(`${nameFor(title)}.base`, textNow()));
  root.querySelector('[data-board-act="copy"]').addEventListener('click', (e) => {
    navigator.clipboard?.writeText(textNow()).then(() => { e.target.textContent = 'Copied'; }, () => { e.target.textContent = 'Copy failed'; });
  });
  if (starter) {
    root.querySelector('[data-board-act="duplicate"]').addEventListener('click', async () => {
      try { go('dashboards', { board: await s.create({ title: `${title} copy`, text: textNow() }) }); } catch (err) { showProblem(`No copy: ${err.message}`); }
    });
  } else {
    root.querySelector('[data-board-act="delete"]').addEventListener('click', async () => {
      if (!confirm(`Delete “${title}”? ${own.draft || s.kind !== 'host' ? 'It is only in this browser.' : 'The host keeps it in its .trash/ folder.'}`)) return;
      const r = own.draft ? await local.remove(own.draft) : await s.remove(own.id, own.etag);
      if (r.conflict) { showProblem('Not deleted: it changed since you opened it (another tab or person). Reload to see theirs.'); return; }
      if (r.error) { showProblem(`Not deleted: ${r.error}`); return; }
      go('dashboards');
    });
    root.querySelector('[data-board-act="edit"]').addEventListener('click', () => editDialog(root, text, async (next) => {
      try { parseBase(next); } catch (err) { return err.message; }
      if (!(await draw(next))) return errorBox.textContent;
      if (!(await keep(next))) return 'Not saved (see the note above the dashboard)';
      return null;
    }));
    root.querySelector('[data-board-act="add"]').addEventListener('click', () => addDialog(root, feed, async (panel) => {
      const next = stringifyBase(withPanel(parseBase(text), panel));
      if (!(await draw(next))) return;
      await keep(next);
    }));
  }
  return { update: (p) => (p.board === params.board ? null : false), unmount() { off(); removeEventListener('dx-board-saved', onSaved); if (pending) pending.cancel(); if (opened) opened.release(); } };
}

function alertIn(root, message) {
  let box = root.querySelector('.dx-alert');
  if (!box) { box = document.createElement('p'); box.className = 'dx-error dx-alert'; box.style.padding = '0 16px'; root.prepend(box); }
  box.textContent = message;
}

/** The .base with one more panel: its binding (a dataset per kind and entity), its view, and a layout entry. */
export function withPanel(base, { type, path, name, field, unit }) {
  const out = { ...base, datasets: { ...(base.datasets || {}) }, views: [...(base.views || [])], layout: { columns: 12, rowHeight: 96, ...(base.layout || {}) } };
  out.layout.panels = [...(out.layout.panels || [])];
  const kind = { plot: 'samples', kpi: 'samples', levels: 'levels', table: 'checks', playback: 'summary' }[type];
  const dsName = type === 'playback' ? 'summary' : `${kind}_${slug(path).replace(/-/g, '_').slice(-40)}`;
  const data = `../datasets/${kind}.json${type === 'playback' ? '' : `?path=${path}`}`;
  if (!Object.values(out.datasets).some((d) => (typeof d === 'string' ? d : d && d.data) === data)) out.datasets[dsName] = { data };
  const from = Object.entries(out.datasets).find(([, d]) => (typeof d === 'string' ? d : d && d.data) === data)[0];
  const taken = new Set(out.views.map((v) => v.name));
  let viewName = name;
  for (let n = 2; taken.has(viewName); n += 1) viewName = `${name} ${n}`;
  const view = { type, name: viewName, from };
  if (type === 'plot') Object.assign(view, { at: '30s', plot: { y: ['value'], series: 'key', ...(field ? { only: [field] } : {}), ...(unit ? { unit } : {}) } });
  // WHY limit 4000: KPI cards are grouped in the order their rows first come, and a capped window (2,000 rows by default)
  // starts in the middle of one sample of every key, so the cards would change places; under the cap they keep the host's order
  if (type === 'kpi') Object.assign(view, { at: '20s', limit: 4000, groupBy: { property: 'label' }, ...(field ? { filters: `key == "${field}"` } : {}), kpi: { value: { property: 'value' }, spark: { x: 't', y: 'value' } } });
  if (type === 'levels') Object.assign(view, { at: '5m', levels: LEVELS });
  if (type === 'table') Object.assign(view, { filters: `path == "${path}"`, order: ['name', 'health', 'message', 'code', 'received'], sort: [{ property: 'rank', direction: 'DESC' }] });
  out.views.push(view);
  out.layout.panels.push({ view: viewName, w: type === 'playback' ? 12 : type === 'kpi' ? 6 : 12, h: type === 'playback' ? 1 : 3 });
  return out;
}

function addDialog(root, feed, onAdd) {
  let dlg = root.querySelector('#dx-add-panel');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'dx-add-panel';
    dlg.className = 'dx-dialog';
    root.append(dlg);
  }
  const rows = feed.dataset('entities').snapshot(Infinity, { at: 'latest', limit: 5000 }).filter((r) => r.claimed || r.rank > 0)
    .sort((a, b) => String(a.path).localeCompare(String(b.path)));
  dlg.innerHTML = `<form method="dialog"><h2>Add panel</h2>
    <label>What <select name="type"><option value="plot">A plot of a State value</option><option value="kpi">State as cards</option><option value="levels">The health history</option><option value="table">The checks</option><option value="playback">The time bar</option></select></label>
    <label>Of <select name="entity">${rows.map((r) => `<option value="${esc(r.path)}">${esc(r.name)} · ${esc(r.path)}</option>`).join('')}</select></label>
    <label>Value <select name="field"><option value="">every value</option></select></label>
    <div class="dx-row"><button type="button" class="dx-btn" value="cancel" data-add-panel="cancel">Cancel</button><button type="submit" class="dx-btn dx-primary" data-add-panel="ok">Add</button></div></form>`;
  const form = dlg.querySelector('form');
  const type = form.elements.type, entity = form.elements.entity, field = form.elements.field;
  async function fields() {
    field.innerHTML = '<option value="">every value</option>';
    const path = entity.value;
    if (!path) return;
    // the entity's State keys: watched for a moment (its feed reads /node, then /values), let go at once
    feed.use('peek', path);
    const e = await feed.whenReady(path, 2000);
    feed.release('peek', path);
    if (entity.value !== path) return;
    for (const [key, info] of (e && e.keys) || []) field.insertAdjacentHTML('beforeend', `<option value="${esc(key)}" data-unit="${esc(info.unit || '')}">${esc(info.unit ? `${key} (${info.unit})` : key)}</option>`);
  }
  entity.addEventListener('change', fields);
  dlg.querySelector('[data-add-panel="cancel"]').addEventListener('click', () => dlg.close());
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    const path = entity.value;
    const row = rows.find((r) => r.path === path);
    const opt = field.selectedOptions[0];
    const what = { plot: 'plot', kpi: 'State', levels: 'health', table: 'checks', playback: 'time' }[type.value];
    const name = type.value === 'playback' ? 'Time' : `${(row && row.name) || path} · ${field.value || what}`;
    dlg.close();
    onAdd({ type: type.value, path, name, field: field.value || '', unit: opt ? opt.dataset.unit || '' : '' });
  });
  dlg.showModal();
  fields();
}

function editDialog(root, text, onApply) {
  let dlg = root.querySelector('#dx-edit');
  if (!dlg) {
    dlg = document.createElement('dialog');
    dlg.id = 'dx-edit';
    dlg.className = 'dx-dialog';
    root.append(dlg);
  }
  dlg.innerHTML = `<form method="dialog"><h2>Edit .base</h2><textarea name="text" spellcheck="false" aria-label="The .base text"></textarea>
    <p class="dx-error" hidden></p>
    <div class="dx-row"><button type="button" class="dx-btn" data-edit="cancel">Cancel</button><button type="submit" class="dx-btn dx-primary" data-edit="apply">Apply</button></div></form>`;
  const area = dlg.querySelector('textarea');
  const err = dlg.querySelector('.dx-error');
  area.value = text;
  dlg.querySelector('[data-edit="cancel"]').addEventListener('click', () => dlg.close());
  dlg.querySelector('form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const problem = await onApply(area.value);
    if (problem) { err.hidden = false; err.textContent = problem; return; }
    dlg.close();
  });
  dlg.showModal();
}
