/* Dashboards: the starters (dashboards/*.base, listed in dashboards/index.json) and your own (docs/peps/0001-robotdx-on-data-ui.md §12.3).

   A dashboard is a data-ui .base with layout: (PEP 0004 / 0005 grammar), drawn by a stock <data-dashboard> over the
   page's live datasets (app.js openBase). Yours live in this browser (localStorage robotdx.dx.boards, every access in
   try/catch) until the host has a place for them: there is no /api/v1/dashboards (asked of the RobotDx session), so
   Save as file downloads the .base and Load .base reads one back (a picked file or one dropped on the page).
   WHY a store with one shape ({id, title, text, updated}): a server endpoint can take the same list later and nothing
   else changes (the reversible default, PEP §12.3).

   Make one: New (an empty dashboard with the time bar), Add panel (a plot, levels, KPI cards or a table of one entity,
   or the playback card), Edit .base (the text itself), Arrange (data-ui's: drag and resize; the arrangement is written
   back into your dashboard's text, so it travels with Save as file). A starter is read only: Duplicate makes it yours. */

import { esc, slug } from '../vendor/data-ui/data-ui.js';

const KEY = 'robotdx.dx.boards';
const NEW_TEXT = `# A dashboard of your own (RobotDx on data-ui). Add panel puts views here, Edit .base changes this text, and
# Arrange drags and resizes the panels. It lives in this browser until you Save as file (a .base anyone can load).
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

export class BoardStore {
  list() {
    try { const v = JSON.parse(localStorage.getItem(KEY) || '[]'); return Array.isArray(v) ? v.filter((b) => b && b.id && typeof b.text === 'string') : []; } catch (_) { return this._memory || []; }
  }
  _write(list) {
    this._memory = list;
    try { localStorage.setItem(KEY, JSON.stringify(list)); } catch (_) { /* private window: this visit only */ }
  }
  get(id) { return this.list().find((b) => b.id === id) || null; }
  save(board) {
    const list = this.list().filter((b) => b.id !== board.id);
    list.push({ ...board, updated: new Date().toISOString() });
    this._write(list);
  }
  remove(id) { this._write(this.list().filter((b) => b.id !== id)); }
  /** A new dashboard of yours; returns its id. */
  create({ title, text }) {
    let id = `my-${slug(title || 'dashboard') || 'dashboard'}`;
    const taken = new Set(this.list().map((b) => b.id));
    for (let n = 2; taken.has(id); n += 1) id = `my-${slug(title || 'dashboard')}-${n}`;
    this.save({ id, title: title || 'Untitled dashboard', text });
    return id;
  }
}

const boards = new BoardStore();
window.__dxBoards = boards;

async function starters(BOARDS) {
  try { return await (await fetch(new URL('index.json', BOARDS), { cache: 'no-store' })).json(); } catch (_) { return []; }
}

/** Read .base files (picked or dropped) into your dashboards; returns how many went in, or throws the first error. */
async function loadFiles(files, parseBase) {
  let n = 0;
  for (const file of files) {
    const text = await file.text();
    parseBase(text);   // WHY first: a file that does not parse is refused with its error, never stored half-read
    boards.create({ title: file.name.replace(/\.base$/i, ''), text });
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
  // a .base dropped anywhere on the destination becomes one of yours (the list redraws to show it)
  const onDrag = (e) => { if ([...(e.dataTransfer?.types || [])].includes('Files')) { e.preventDefault(); root.classList.add('dx-drop'); } };
  const onLeave = () => root.classList.remove('dx-drop');
  const onDrop = async (e) => {
    const files = [...(e.dataTransfer?.files || [])].filter((f) => /\.base$/i.test(f.name) || f.type.startsWith('text/'));
    if (!files.length) return;
    e.preventDefault();
    root.classList.remove('dx-drop');
    try { await loadFiles(files, parseBase); } catch (err) { alertIn(root, `That file is not a .base this page can read: ${err.message}`); return; }
    if (params.board) go('dashboards'); else drawList();
  };
  root.addEventListener('dragenter', onDrag);
  root.addEventListener('dragover', onDrag);
  root.addEventListener('dragleave', onLeave);
  root.addEventListener('drop', onDrop);
  const off = () => { root.removeEventListener('dragenter', onDrag); root.removeEventListener('dragover', onDrag); root.removeEventListener('dragleave', onLeave); root.removeEventListener('drop', onDrop); };

  function drawList() {
    const mine = boards.list().sort((a, b) => String(b.updated).localeCompare(String(a.updated)));
    root.innerHTML = `<div class="dx-boards">
      <div class="dx-bar"><h1>Dashboards</h1><span class="dx-grow"></span>
        <button type="button" class="dx-btn dx-primary" data-board-act="new">New dashboard</button>
        <button type="button" class="dx-btn" data-board-act="load">Load .base…</button>
        <input type="file" id="dx-load-input" accept=".base,text/plain" multiple hidden>
      </div>
      <section><h2>Starters</h2><ul class="dx-board-list">${list.map((s) => `<li><a href="?go=dashboards&board=${esc(s.id)}" data-board-id="${esc(s.id)}" data-board-kind="starter"><b>${esc(s.title)}</b><small>${esc(s.about || s.file)}</small></a></li>`).join('')}</ul></section>
      <section><h2>Yours</h2>${mine.length ? `<ul class="dx-board-list">${mine.map((b) => `<li><a href="?go=dashboards&board=${esc(b.id)}" data-board-id="${esc(b.id)}" data-board-kind="mine"><b>${esc(b.title)}</b><small>in this browser · ${esc(String(b.updated || '').replace('T', ' ').slice(0, 16))}</small></a></li>`).join('')}</ul>`
        : '<p class="dx-empty" style="padding:0">None yet: New dashboard, or Load .base (or drop a .base file on this page).</p>'}</section>
    </div>`;
    root.querySelector('[data-board-act="new"]').addEventListener('click', () => go('dashboards', { board: boards.create({ title: 'Untitled dashboard', text: NEW_TEXT }) }));
    const input = root.querySelector('#dx-load-input');
    root.querySelector('[data-board-act="load"]').addEventListener('click', () => input.click());
    input.addEventListener('change', async () => {
      try { await loadFiles([...input.files], parseBase); } catch (err) { alertIn(root, `That file is not a .base this page can read: ${err.message}`); return; }
      drawList();
    });
    for (const a of root.querySelectorAll('[data-board-id]')) {
      a.addEventListener('click', (e) => { if (e.button === 0 && !e.metaKey && !e.ctrlKey && !e.shiftKey) { e.preventDefault(); go('dashboards', { board: a.dataset.boardId }); } });
    }
    showTimeBar(true);
  }

  if (!params.board) {
    drawList();
    return { update: (p) => (p.board ? false : (drawList(), null)), unmount: off };
  }

  // ---- one dashboard ------------------------------------------------------------------------------------------------------
  const starter = list.find((s) => s.id === params.board);
  const own = starter ? null : boards.get(params.board);
  if (!starter && !own) {
    root.innerHTML = `<p class="dx-empty">No dashboard “${esc(params.board)}” here (yours live in the browser that made them). <a class="dx-link" href="?go=dashboards">All dashboards</a></p>`;
    return { update: () => false, unmount: off };
  }
  const title = starter ? starter.title : own.title;
  let text = starter ? await (await fetch(new URL(starter.file, BOARDS), { cache: 'no-store' })).text() : own.text;
  root.innerHTML = `<div class="dx-board-view">
    <div class="dx-bar"><a class="dx-link" href="?go=dashboards" data-back>‹ Dashboards</a><h1>${esc(title)}</h1>
      <span class="dx-note">${starter ? `starter · ${esc(starter.file)}` : 'yours · in this browser'}</span><span class="dx-grow"></span>
      ${starter ? '<button type="button" class="dx-btn" data-board-act="duplicate" title="Make a copy of your own to change">Duplicate</button>'
        : '<button type="button" class="dx-btn" data-board-act="add">Add panel</button><button type="button" class="dx-btn" data-board-act="edit">Edit .base</button>'}
      <button type="button" class="dx-btn" data-board-act="save" title="Download this dashboard as a .base file (the arrangement is its layout:)">Save as file</button>
      <button type="button" class="dx-btn" data-board-act="copy" title="Put the .base text on the clipboard">Copy .base</button>
      ${own ? '<button type="button" class="dx-btn" data-board-act="delete" title="Delete this dashboard from this browser">Delete</button>' : ''}
    </div>
    <data-dashboard class="dx-board" id="dx-board-${esc(params.board)}"></data-dashboard>
    <p class="dx-error" id="dx-board-error" hidden></p>
  </div>`;
  root.querySelector('[data-back]').addEventListener('click', (e) => { e.preventDefault(); go('dashboards'); });
  const el = root.querySelector('data-dashboard');
  const errorBox = root.querySelector('#dx-board-error');
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

  // Arrange (data-ui's) writes your arrangement back into your text, so Save as file carries it
  el.addEventListener('data-dashboard-layout', (e) => {
    if (!own) return;
    text = e.detail.text;
    boards.save({ ...boards.get(own.id), text });
    // WHY forget data-ui's own copy of the arrangement: the text holds it now, and a later Edit .base must win
    try { for (const k of Object.keys(localStorage)) if (k.startsWith('data-ui/dash-layout:') && k.endsWith(`/${el.id}`)) localStorage.removeItem(k); } catch (_) { /* nothing stored */ }
  });
  const textNow = () => (own ? text : (el.layoutText ? el.layoutText() : text));
  root.querySelector('[data-board-act="save"]').addEventListener('click', () => download(`${params.board}.base`, textNow()));
  root.querySelector('[data-board-act="copy"]').addEventListener('click', (e) => {
    navigator.clipboard?.writeText(textNow()).then(() => { e.target.textContent = 'Copied'; }, () => { e.target.textContent = 'Copy failed'; });
  });
  if (starter) {
    root.querySelector('[data-board-act="duplicate"]').addEventListener('click', () => go('dashboards', { board: boards.create({ title: `${title} (copy)`, text: textNow() }) }));
  } else {
    root.querySelector('[data-board-act="delete"]').addEventListener('click', () => {
      if (!confirm(`Delete “${title}” from this browser? (Save as file first to keep a copy.)`)) return;
      boards.remove(own.id);
      go('dashboards');
    });
    root.querySelector('[data-board-act="edit"]').addEventListener('click', () => editDialog(root, text, async (next) => {
      try { parseBase(next); } catch (err) { return err.message; }
      if (!(await draw(next))) return errorBox.textContent;
      text = next;
      boards.save({ ...boards.get(own.id), text });
      return null;
    }));
    root.querySelector('[data-board-act="add"]').addEventListener('click', () => addDialog(root, feed, async (panel) => {
      const base = parseBase(text);
      const next = stringifyBase(withPanel(base, panel));
      if (!(await draw(next))) return;
      text = next;
      boards.save({ ...boards.get(own.id), text });
    }));
  }
  return { update: (p) => (p.board === params.board ? null : false), unmount() { off(); if (opened) opened.release(); } };
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
  if (type === 'kpi') Object.assign(view, { at: '30s', groupBy: { property: 'label' }, sort: [{ property: 'order', direction: 'ASC' }], ...(field ? { filters: `key == "${field}"` } : {}), kpi: { value: { property: 'value' }, spark: { x: 't', y: 'value' } } });
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
