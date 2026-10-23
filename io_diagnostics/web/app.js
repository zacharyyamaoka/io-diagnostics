// IO diagnostics page: renders what the host computes (docs/api-v1.md is the only contract it relies on).
// Levels, staleness, rollup and knock-on are decided server-side (contract/record.py via io_diagnostics/tree.py);
// this file lays the same rows out three ways and never talks to hardware. Started from V6's page.
//
// Round 2 (docs/round2-seams.md): a STRUCTURE (window.IOStructures, view/structure/*.js) turns the API state into
// display rows through view/core.js; the Tree view (a tree style, window.IOTreeRenderers, tree/*.js) and the
// Table both draw those rows, never the raw API tree (the legacy styles v3-v5 still draw the physical API tree).
// A SKIN (skins/*.css) sets the tokens of tokens.css for light and dark. Structure, tree style, skin and
// light/dark switch live from the quiet control cluster at the bottom-right and persist in this browser.
// The page owns the data, the selection, the detail panel, the table and the activity tab; a renderer owns its
// own rows' drawing, expansion, keys and clicks.
'use strict';

const params = new URLSearchParams(location.search);
const S = {
  view: params.get('view') || 'tree',
  sel: params.get('sel'),
  query: '',
  data: null,          // /api/v1/state
  act: null,           // /api/v1/activity
  actAt: 0,
  node: null,          // /api/v1/node for S.sel
  nodeAt: 0,
  open: new Map(),     // row key -> true/false, for the table and the built-in fallback tree
  selKey: null,        // the selected row when it stands for no entity (a structure's own grouping row)
  built: null,         // IOView.build(...) of the active structure for the current state
  structure: null,     // active structure id (view/structure/*.js)
  toggles: {},         // structure id -> {option key: bool}: the structure's own declared toggles
  skins: [],           // ready skins from skins/index.json
  skin: null,
  mode: 'auto',        // 'auto' (prefers-color-scheme) | 'light' | 'dark'
  dialog: params.get('dialog') ? { name: params.get('dialog') } : null,
  chip: params.get('chip') === '1',
  lastOk: 0,
  lastError: '',
  variant: null,       // the tree proposal drawing the Tree view (set by setupSwitcher)
};
const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const icon = (name, cls = 'ic') => `<svg class="${cls}"><use href="#i-${name || 'device'}"/></svg>`;
const lvl = (level) => level || 'none';
const pill = (level) => `<span class="pill ${lvl(level)}">${level || '—'}</span>`;
// Rule 3 + 4 (Zach, 2026-10-22): a problem is a SHAPE; OK draws nothing at all.
const shape = (level, title = '') => (PROBLEM.has(level)
  ? `<svg class="shape lv-${level}" role="img" aria-label="${esc(title || level)}"><use href="#s-${level}"/></svg>` : '');
const ago = (s) => (s === null || s === undefined ? '—' : s < 90 ? `${Math.round(s)} s` : s < 3600 ? `${Math.floor(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
const isGrouping = (node) => !node.own || node.own.level === null || node.own.level === undefined;

// ---------------------------------------------------------------------------- preferences ----
// Every choice persists in this browser; storage may be blocked (private mode), then it lasts this page only.
const PREFS = {
  structure: 'io-diagnostics.structure',
  variant: 'io-diagnostics.tree-variant',
  skin: 'io-diagnostics.skin',
  mode: 'io-diagnostics.mode',
  toggles: 'io-diagnostics.structure-toggles',
};
function readPref(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value); } catch (_) { /* private mode: the choice lasts this page only */ }
}

// ---------------------------------------------------------------------------------- data ----
async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (data.error) throw new Error(data.error);
  return data;
}

// --------------------------------------------------------------- skins, structures (manifests) ----
// skins/index.json and view/structure/index.json pre-list every planned entry; only `ready` ones are loaded, so a
// lane that lands a skin or a structure flips its own line and nothing else (docs/round2-seams.md).
async function readManifest(url, listKey) {
  try {
    const manifest = await getJson(url);
    return { default: manifest.default, entries: (manifest[listKey] || []).filter((entry) => entry.ready) };
  } catch (error) {
    console.warn(`manifest ${url}:`, error);
    return { default: null, entries: [] };
  }
}

function loadStylesheet(href, id) {
  if (document.querySelector(`link[data-skin-css="${id}"]`)) return Promise.resolve();
  return new Promise((resolve) => {
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    link.dataset.skinCss = id;
    link.onload = link.onerror = () => resolve();
    document.head.appendChild(link);
  });
}

function loadScript(src) {
  if (document.querySelector(`script[src="${src}"]`)) return Promise.resolve();
  return new Promise((resolve) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = script.onerror = () => resolve();
    document.head.appendChild(script);
  });
}

const DEFAULTS = { skin: 'claude', structure: 'p0', variant: 'v6' };
const darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

// <html data-skin data-mode>: the skin's tokens for the mode. Auto follows the OS until the viewer picks one.
function applyTheme() {
  const html = document.documentElement;
  html.dataset.skin = S.skin || DEFAULTS.skin;
  html.dataset.mode = S.mode === 'light' || S.mode === 'dark' ? S.mode : (darkQuery && darkQuery.matches ? 'dark' : 'light');
  html.dataset.modePref = S.mode;
}

async function tick() {
  try {
    S.data = await getJson('/api/v1/state');
    S.lastOk = performance.now();
    S.lastError = '';
    if (!S.sel) S.sel = (S.data.summary.attention[0] || {}).path || S.data.tree.path;
    if (performance.now() - S.actAt > 4000 || !S.act) {
      S.actAt = performance.now();
      getJson('/api/v1/activity').then((act) => { S.act = act; render(); }).catch(() => {});
    }
    if (S.sel && (performance.now() - S.nodeAt > 1800 || !S.node || S.node.node.path !== S.sel)) loadNode();
    render();
  } catch (error) {
    // WHY not wipe the left pane: the tree panes hold each proposal's expansion; a host restart is one blip
    S.lastError = String(error);
    if (!S.data) { $('tree-host').hidden = true; $('left-other').hidden = false; $('left-other').innerHTML = `<div class="err">${esc(error)}</div>`; }
    renderStatusbar();
  }
}

function loadNode() {
  const path = S.sel;
  S.nodeAt = performance.now();
  getJson(`/api/v1/node?path=${encodeURIComponent(path)}`)
    .then((node) => { if (S.sel === path) { S.node = node; render(); } })
    .catch(() => { if (S.sel === path) { S.sel = S.data.tree.path; S.node = null; } });
}

function index(tree) {
  const byPath = new Map();
  (function visit(node) { byPath.set(node.path, node); node.children.forEach(visit); })(tree);
  return byPath;
}

// ------------------------------------------------------------------------------ tree styles ----
// Each renderer: window.IOTreeRenderers[id] = {name, description?, consumes?, render(container, data, opts)}.
// consumes: 'rows' -> data is the active structure's top-level rows (view/core.js), opts {onSelect(rowKey),
// onSelectEntity(entityPath), selectedPath: rowKey, query, summary, host}. Without it the renderer is legacy and
// gets the raw API tree with {onSelect(entityPath), selectedPath: entityPath, query}. render() is idempotent,
// so the page calls it on every 1 s tick with the same persistent pane.
const VARIANT_KEY = PREFS.variant;
const DEFAULT_VARIANT = DEFAULTS.variant;
const VARIANT_ORDER = ['v6', 'v1', 'v2', 'v3', 'v4', 'v5'];
// v5 draws whatever tree it is given and has no search of its own: the page says so instead of a box that does nothing.
const NO_QUERY = new Set(['v5']);
// v5 is the tree that is also the table: its ID / last-seen columns appear from 600 px and all of them from 840 px.
const WIDE = new Set(['v5']);
const scrollTops = new Map();  // variant -> the tree pane's scroll position, restored on switching back

const renderers = () => window.IOTreeRenderers || {};
const orderOf = (id) => (VARIANT_ORDER.includes(id) ? VARIANT_ORDER.indexOf(id) : VARIANT_ORDER.length);
const variantIds = () => Object.keys(renderers()).filter((id) => typeof renderers()[id].render === 'function')
  .sort((a, b) => orderOf(a) - orderOf(b) || a.localeCompare(b));
const isLegacy = (id) => Boolean(renderers()[id]) && renderers()[id].consumes !== 'rows';

const storedVariant = () => readPref(VARIANT_KEY);
const storeVariant = (id) => writePref(VARIANT_KEY, id);

function paneFor(id) {
  const host = $('tree-host');
  let pane = [...host.children].find((element) => element.dataset.variant === id);
  if (!pane) {
    pane = document.createElement('div');
    pane.className = 'tree-pane';
    pane.dataset.variant = id;
    host.appendChild(pane);
  }
  return pane;
}

// a rows renderer reports a row key; legacy renderers report an entity path
function onTreeSelect(key) {
  const row = S.built && S.built.byKey.get(key);
  if (row && !row.entity) { // a structure's own grouping row: nothing to load from /api/v1/node
    if (S.selKey === key) return;
    S.selKey = key; S.sel = null; S.node = null;
    render();
    return;
  }
  const entity = row ? row.entity : key;
  if (!entity || (entity === S.sel && !S.selKey)) return;
  select(entity);
  render();
}
function onSelectEntity(entity) {
  if (!entity || entity === S.sel) return;
  select(entity);
  render();
}
// the row key a rows renderer should mark selected
const selectedKey = () => S.selKey || (S.built && S.sel ? S.built.byEntity.get(S.sel) || null : null);

function renderTreePane() {
  const id = S.variant;
  const renderer = id && renderers()[id];
  const host = $('tree-host');
  if (!renderer) { host.innerHTML = renderTree(); return; } // no proposal loaded: the page's own plain list
  for (const element of host.children) element.hidden = element.dataset.variant !== id;
  const pane = paneFor(id);
  pane.hidden = false;
  try {
    if (isLegacy(id)) renderer.render(pane, S.data.tree, { onSelect: onTreeSelect, selectedPath: S.sel, query: NO_QUERY.has(id) ? '' : S.query });
    else {
      renderer.render(pane, S.built.rows, { onSelect: onTreeSelect, onSelectEntity, selectedPath: selectedKey(), query: S.query,
        summary: S.data.summary, host: S.built.host, structure: S.structure });
    }
  } catch (error) {
    console.error(`tree ${id}:`, error);
    pane.innerHTML = `<div class="err">tree ${esc(id)} failed: ${esc(error)}</div>`;
  }
}

// ------------------------------------------------------------------ the bottom-right controls ----
// One quiet cluster (Zach: never make him edit a URL flag): structure (+ its own toggles), tree style, skin,
// Auto / Light / Dark. Every choice switches live and persists.
function setupSwitcher() {
  const ids = variantIds();
  const stored = storedVariant();
  S.variant = ids.includes(stored) ? stored : ids.includes(DEFAULT_VARIANT) ? DEFAULT_VARIANT : ids[0] || null;
  const picker = $('tree-variant');
  picker.innerHTML = ids.map((id) => `<option value="${esc(id)}" title="${esc(renderers()[id].description || '')}">`
    + `${esc(id)} · ${esc(renderers()[id].name || id)}${isLegacy(id) ? ' (legacy)' : ''}</option>`).join('');
  picker.value = S.variant || '';
  $('variant-switch').hidden = ids.length < 2;
  picker.addEventListener('change', () => switchVariant(picker.value));
}

function setupStructures(manifestDefault) {
  const ids = IOView.structureIds();
  const stored = readPref(PREFS.structure);
  const fallback = ids.includes(manifestDefault) ? manifestDefault : ids.includes(DEFAULTS.structure) ? DEFAULTS.structure : ids[0] || null;
  S.structure = ids.includes(stored) ? stored : fallback;
  try { S.toggles = JSON.parse(readPref(PREFS.toggles) || '{}') || {}; } catch (_) { S.toggles = {}; }
  const picker = $('structure');
  picker.innerHTML = ids.map((id) => `<option value="${esc(id)}" title="${esc(window.IOStructures[id].description || '')}">`
    + `${esc(id)} · ${esc(window.IOStructures[id].name || id)}</option>`).join('');
  picker.value = S.structure || '';
  picker.addEventListener('change', () => {
    if (!window.IOStructures[picker.value]) return;
    S.structure = picker.value;
    writePref(PREFS.structure, S.structure);
    S.open.clear();
    renderToggles();
    render();
  });
  renderToggles();
}

// the active structure's own toggles (e.g. p0 "Parts as rows"), as quiet check chips in the cluster
function structureOptions() {
  const structure = window.IOStructures[S.structure] || {};
  const chosen = S.toggles[S.structure] || {};
  const options = {};
  for (const toggle of structure.toggles || []) options[toggle.key] = toggle.key in chosen ? Boolean(chosen[toggle.key]) : Boolean(toggle.default);
  return options;
}
function renderToggles() {
  const structure = window.IOStructures[S.structure] || {};
  const options = structureOptions();
  $('structure-toggles').innerHTML = (structure.toggles || []).map((toggle) => `<label class="ctl-check" title="${esc(toggle.title || toggle.label)}">`
    + `<input type="checkbox" data-opt="${esc(toggle.key)}"${options[toggle.key] ? ' checked' : ''}>${esc(toggle.label)}</label>`).join('');
}
function setToggle(key, value) {
  S.toggles[S.structure] = { ...(S.toggles[S.structure] || {}), [key]: value };
  writePref(PREFS.toggles, JSON.stringify(S.toggles));
  render();
}

function setupSkins(manifestDefault) {
  const ids = S.skins.map((skin) => skin.id);
  const stored = readPref(PREFS.skin);
  S.skin = ids.includes(stored) ? stored : ids.includes(manifestDefault) ? manifestDefault : ids[0] || DEFAULTS.skin;
  const picker = $('skin');
  picker.innerHTML = S.skins.map((skin) => `<option value="${esc(skin.id)}">${esc(skin.name || skin.id)}</option>`).join('');
  picker.value = S.skin;
  picker.addEventListener('change', () => { S.skin = picker.value; writePref(PREFS.skin, S.skin); applyTheme(); });
  const storedMode = readPref(PREFS.mode);
  S.mode = ['auto', 'light', 'dark'].includes(storedMode) ? storedMode : 'auto';
  if (darkQuery) darkQuery.addEventListener('change', () => { if (S.mode === 'auto') { applyTheme(); renderModeSwitch(); } });
  applyTheme();
  renderModeSwitch();
}
function renderModeSwitch() {
  document.querySelectorAll('#mode-switch button').forEach((button) => {
    const on = button.dataset.mode === S.mode;
    button.classList.toggle('on', on);
    button.setAttribute('aria-checked', String(on));
  });
  $('mode-switch').title = `Light / dark: ${S.mode}${S.mode === 'auto' ? ` (the OS says ${document.documentElement.dataset.mode})` : ''}`;
}
function setMode(mode) {
  S.mode = mode;
  writePref(PREFS.mode, mode);
  applyTheme();
  renderModeSwitch();
}

function switchVariant(id) {
  if (!renderers()[id]) return;
  storeVariant(id); // WHY before the same-value return: an explicit pick of the default must survive a new default
  if (id === S.variant) return;
  scrollTops.set(S.variant, $('left').scrollTop);
  S.variant = id;
  S.view = 'tree'; // a proposal only draws the Tree view: show the change at once
  render();
  $('left').scrollTop = scrollTops.get(id) || 0;
}

// ------------------------------------------------------- the page's own rows (table + fallback) --
// The table and the fallback list draw the active structure's rows with the tree's row grammar: chevron, type icon
// and label together, a folded part's words, the status shape at the right end of the name column; same nesting.
const isOpen = (row) => row.children.length > 0 && (S.query.trim() ? true : S.open.has(row.key) ? S.open.get(row.key) : row.defaultOpen);

function visibleRows(rows) {
  const out = [];
  (function walk(list) {
    for (const row of list) {
      out.push(row);
      if (isOpen(row)) walk(row.children);
    }
  })(rows);
  return out;
}

// The row's own status mark (black box): its own problem, else a folded part's own problem; never the hint.
function statusMark(row) {
  const status = row.status || {};
  if (status.own) {
    // a folded part worse than the own report badges the shape (as tree style v6)
    const worse = status.part && IOView.RANK[status.part.level] > IOView.RANK[status.own] ? `<i class="badge lv-${status.part.level}" title="${esc(status.part.text)}"></i>` : '';
    return `<span class="st own${row.knock_on_of ? ' knock' : ''}">${shape(status.own, row.message)}${worse}</span>`;
  }
  if (status.part) return `<span class="st part${status.part.knock_on_of ? ' knock' : ''}">${shape(status.part.level, status.part.text)}</span>`;
  return '<span class="st"></span>';
}

function rowHtml(row, selectable) {
  const open = isOpen(row);
  const chev = row.children.length
    ? `<span class="chev${open ? ' open' : ''}" data-toggle="${esc(row.key)}" title="${open ? 'Collapse' : 'Expand'}"><svg class="ic"><use href="#i-chev"/></svg></span>`
    : '<span class="chev leaf"></span>';
  const cls = ['row', row.connected === false ? 'off' : '', row.knock_on_of ? 'knock' : '', row.hit ? 'hit' : '',
    selectable && row.key === selectedKey() ? 'sel' : ''].join(' ');
  const note = row.status && row.status.note ? `<span class="note">${esc(row.status.note)}</span>` : '';
  return `<div class="${cls}" data-key="${esc(row.key)}" style="--d:${row.depth}" title="${esc(row.title || row.label)}">${chev}${icon(row.icon)}`
    + `<span class="name">${esc(row.label)}</span>${note}<span class="fill"></span>${statusMark(row)}</div>`;
}

// Fallback only: drawn when no tree style registered (e.g. a tree/*.js failed to load).
function renderTree() {
  const counts = S.data.summary.counts;
  const text = ['error', 'stale', 'unknown', 'warn'].filter((l) => counts[l]).map((l) => `${counts[l]} ${l}`).join(' · ') || 'nothing needs you';
  return `<div class="tree-head"><span>Entities · ${S.data.summary.entities}</span><span>${text}</span></div>`
    + visibleRows(S.built.rows).map((row) => rowHtml(row, true)).join('');
}

// ------------------------------------------------------------------------------ table mode --
function fmtSeen(row) {
  if (row.age_s === null || row.age_s === undefined) return '<span class="muted">—</span>';
  const period = row.expected_period_s ? ` / ${row.expected_period_s} s` : '';
  return `<span title="last received ${esc(row.last_received)}">${ago(row.age_s)} ago${period}</span> ${row.stale ? shape('stale', 'stale') : ''}`;
}

// Message column: the row's own words; a problem row's own report reads in full text colour, everything else quiet.
function messageCell(row) {
  const own = row.status && row.status.own;
  const word = own ? `<span class="lvword lv-${own}${row.knock_on_of ? ' knock' : ''}">${esc(own)}</span> ` : '';
  return `<td title="${esc(row.message)}" class="${own && !row.knock_on_of ? '' : 'quiet'}">${word}${esc(row.message || '')}</td>`;
}

// Below column: the advisory hint (a worse cause below) as the API sent it; quiet text, small shape.
function belowCell(row) {
  const hint = row.status && row.status.hint;
  if (!hint) return '<td></td>';
  return `<td title="${esc(hint.message)}"><span class="belowcell">${shape(hint.level, `below: ${hint.level}`)}<span>${esc(hint.origin_name || hint.message || '')}</span></span></td>`;
}

function renderTable() {
  const rows = visibleRows(S.built.rows);
  const extra = S.built.columns || [];
  const body = rows.map((row) => `<tr class="${row.key === selectedKey() ? 'sel' : ''} ${row.connected === false ? 'off' : ''}" data-key="${esc(row.key)}">`
    + `<td class="namecell">${rowHtml(row, false)}</td>`
    + `<td class="mono idcell" title="${esc(row.id_text || '')}">${esc(row.idText || '')}</td>`
    + messageCell(row)
    + belowCell(row)
    + extra.map((column) => `<td title="${esc(row.columns[column.key] || '')}">${esc(row.columns[column.key] || '')}</td>`).join('')
    + `<td class="num">${fmtSeen(row)}</td>`
    + `<td class="num">${row.checks || ''}</td>`
    + `<td class="num">${row.faults || ''}</td></tr>`).join('');
  const extraCols = extra.map((column) => `<col style="width:${esc(column.width || '160px')}">`).join('');
  const extraHeads = extra.map((column) => `<th>${esc(column.title)}</th>`).join('');
  return `<table class="grid"><colgroup><col style="width:380px"><col style="width:170px"><col><col style="width:190px">${extraCols}<col style="width:140px"><col style="width:60px"><col style="width:60px"></colgroup>`
    + `<thead><tr><th>Name</th><th>ID</th><th>Message</th><th>Below</th>${extraHeads}<th>Last seen</th><th>Checks</th><th>Faults</th></tr></thead>`
    + `<tbody>${body}</tbody></table>`;
}

// --------------------------------------------------------------------------- activity tab --
function eventRow(event) {
  return `<div class="ev" data-go="${esc(event.path)}"><span class="t">${esc(event.time)}</span>`
    + `<span><span class="kind ${esc(event.kind.replace(/ /g, '-'))}">${esc(event.kind)}</span></span><span class="p">${esc(event.port)}</span>`
    + `<span>${esc(event.message)}</span><span class="src" title="${esc(event.source)}">${esc(event.severity === 'info' ? '' : event.severity)}</span></div>`;
}
function renderActivity() {
  const act = S.act;
  if (!act) return '<div class="err muted">loading activity…</div>';
  const lh = act.link_health.map((r) => `<tr data-go="${esc(r.path)}"><td title="${esc(r.edge)}">${esc(r.edge)}</td><td>${esc(r.kind)}</td><td>${esc(r.metric)}</td>`
    + `<td title="${esc(r.value)}"><span class="lhst">${shape(r.level)}</span>${esc(r.value)}</td><td title="${esc(r.source)}">${esc(r.source)}</td></tr>`).join('');
  const faults = act.faults.map((f) => `<tr data-go="${esc(f.path)}"><td title="${esc(f.path)}">${esc(f.name || f.path)}</td><td>${esc(f.code)}</td>`
    + `<td>${pill(f.severity)}</td><td>${esc(f.status)}${f.active ? ' · active' : ''}</td><td>${f.occurrence_count}</td><td title="${esc(f.message)}">${esc(f.message)}</td></tr>`).join('');
  return `<div class="act"><section><h2>Activity log <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· ${act.events.length} events, newest first</span></h2>`
    + (act.events.map(eventRow).join('') || '<div class="muted">No events yet.</div>') + '</section>'
    + `<section><h2>Faults · the host's memory (pending → confirmed → healed)</h2><table class="lh"><colgroup><col style="width:24%"><col style="width:18%"><col style="width:70px"><col style="width:120px"><col style="width:40px"><col></colgroup>`
    + `<thead><tr><th>Entity</th><th>Code</th><th>Severity</th><th>Status</th><th>#</th><th>Message</th></tr></thead><tbody>${faults || '<tr><td colspan="6" class="muted">none</td></tr>'}</tbody></table>`
    + `<h2 style="margin-top:22px">Link health · the computer diagnoses its own wires</h2><table class="lh"><colgroup><col style="width:30%"><col style="width:60px"><col style="width:20%"><col><col style="width:22%"></colgroup>`
    + `<thead><tr><th>Edge</th><th>Kind</th><th>Metric</th><th>Value</th><th>Source</th></tr></thead><tbody>${lh}</tbody></table></section></div>`;
}

// --------------------------------------------------------------------------------- detail ----
function kvCard(title, rows, opts = {}) {
  const body = rows.filter(Boolean).map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`).join('');
  return `<div class="card ${opts.wide ? 'wide' : ''}"><h3>${opts.icon ? icon(opts.icon) : ''}${esc(title)}${opts.right ? `<span class="right">${opts.right}</span>` : ''}</h3><div class="kv">${body}</div></div>`;
}

function checkCard(check) {
  const values = check.values.map((v) => `<tr><td class="k">${esc(v.key)}</td><td>${esc(v.value)}</td></tr>`).join('');
  const measurements = check.measurements.map((m) => `<tr><td class="k">${esc(m.key)}</td><td>${shape(m.level)} ${esc(m.text)}</td></tr>`).join('');
  const timing = check.expected_period_s ? `every ${check.expected_period_s} s · STALE after ${check.stale_after_s} s` : 'event-driven (never stale)';
  return `<div class="card check"><h3>${esc(check.name)}<span class="lvl">${pill(check.effective_level)}</span></h3>`
    + `<div class="msg">${esc(check.message || '—')}${check.code ? ` <span class="mono muted">${esc(check.code)}</span>` : ''}</div>`
    + (values || measurements ? `<table class="vt">${measurements}${values}</table>` : '')
    + `<div class="meta">received ${esc((check.received_iso || '').slice(11, 19) || '—')} · ${ago(check.age_s)} ago · ${esc(timing)}`
    + `${check.level !== check.effective_level ? ` · reported ${esc(check.level)}` : ''}<br>${esc(check.source)}</div></div>`;
}

// A structure's own grouping row (no entity): its members, each one click from its detail.
function renderGroupDetail(row) {
  const members = row.children.map((child) => `<div class="part-row" ${child.entity ? `data-go="${esc(child.entity)}"` : ''}>${icon(child.icon)}`
    + `<span class="pname">${esc(child.label)}</span><span class="pmsg">${esc(child.message || '')}</span>${statusMark(child)}</div>`).join('');
  return `<div class="d-head"><div class="d-title">${icon(row.icon)}<span>${esc(row.label)}</span></div>`
    + `<div class="d-msg">${row.status.hint ? `${esc(row.status.hint.level)} below · ${esc(row.status.hint.message)}` : esc(row.message || 'grouping row')}</div></div>`
    + `<div class="cards"><div class="card wide"><h3>Members · ${row.children.length}</h3>${members || '<div class="muted">none</div>'}</div></div>`;
}

// The folded parts of the selected row (view/core.js keeps them off the tree): each one click from its own detail.
function partsCard(row) {
  if (!row || !row.parts || !row.parts.length) return '';
  const items = row.parts.map((part) => `<div class="part-row" data-go="${esc(part.entity)}" title="${esc(part.name)}">${icon(part.icon)}`
    + `<span class="pname">${esc(part.label)}</span><span class="pmsg">${esc(part.message)}</span>`
    + `<span class="st${part.knock_on_of ? ' knock' : ''}">${shape(part.problem, part.message)}</span></div>`).join('');
  return `<div class="card wide"><h3>Parts · ${row.parts.length}<span class="right muted">composition (part_of), folded into this row</span></h3>${items}</div>`;
}

function renderDetail(byPath) {
  if (!S.sel && S.selKey && S.built && S.built.byKey.has(S.selKey)) return renderGroupDetail(S.built.byKey.get(S.selKey));
  const tnode = byPath.get(S.sel) || S.data.tree;
  const detail = S.node && S.node.node.path === tnode.path ? S.node : null;
  const node = tnode;
  // the row that stands for exactly this entity (not its owner, when the selection is a folded part)
  const rowKey = S.built ? S.built.byEntity.get(node.path) : null;
  const ownRow = rowKey && S.built.byKey.get(rowKey).entity === node.path ? S.built.byKey.get(rowKey) : null;
  const title = ownRow ? ownRow.label : node.name;
  const hint = node.hint ? `<span class="muted"> · below: <a class="link" data-go="${esc(node.hint.origin)}">${esc(node.rollup.origin_name)}</a> is ${esc(node.hint.level)}</span>` : '';
  let html = `<div class="d-head"><div class="d-title">${icon(node.icon)}<span>${esc(title)}</span>${pill(node.level)}</div>`
    + `<div class="d-msg">${esc(node.message || '—')}${hint}</div>`
    + `<div class="d-path">${title !== node.name ? `${esc(node.name)} · ` : ''}${esc(node.path)}${node.hardware_id ? ` · ${esc(node.hardware_id)}` : ''}</div></div>`;
  if (node.knock_on_of) {
    const cause = byPath.get(node.knock_on_of);
    html += `<div class="banner knock">↳ <span>Explained by <a class="link" data-go="${esc(node.knock_on_of)}">${esc(cause ? cause.name : node.knock_on_of)}</a> above: counted once, as that cause.</span></div>`;
  }
  if (node.stale) html += `<div class="banner stale">◷ <span><b>Stale:</b> nothing received for ${ago(node.age_s)} (expected every ${esc(node.expected_period_s)} s). The last values stay visible with their age.</span></div>`;
  if (!node.connected) html += '<div class="banner osonly">Not connected.</div>';
  html += '<div class="cards">';
  if (!detail) return `${html}<div class="muted">loading…</div></div>`;
  const status = [
    ['shows', `${pill(node.level)} <span class="muted">${node.own.level === null ? 'grouping: worst below' : 'its own self-report'}</span>`],
    ['worst below', node.rollup.level ? `${pill(node.rollup.level)} ${node.rollup.origin && node.rollup.origin !== node.path ? `<a class="link" data-go="${esc(node.rollup.origin)}">${esc(node.rollup.origin_name)}</a>` : ''}` : '<span class="muted">nothing reports</span>'],
    Object.keys(node.rollup.counts).length ? ['causes below', Object.entries(node.rollup.counts).map(([l, n]) => `${n} ${l}`).join(' · ')] : null,
    node.rollup.knock_on.length ? ['knock-on', `${node.rollup.knock_on.length} collapsed`] : null,
    ['last received', node.age_s === null ? '<span class="muted">no records of its own</span>' : `${ago(node.age_s)} ago`],
    ['owner', esc(node.owner || '—')],
  ];
  html += kvCard('Status', status);
  const id = node.identity || {};
  const parent = detail.ancestors[detail.ancestors.length - 1];
  const idRows = Object.entries(id).map(([k, v]) => [k.replace('_', ' '), esc(v)]);
  if (parent) idRows.push([node.edge === 'part_of' ? 'part of' : 'via', `<a class="link" data-go="${esc(parent.path)}">${esc(parent.name)}</a>`]);
  if (node.hardware_id) idRows.push(['hardware id', `<span class="mono">${esc(node.hardware_id)}</span>`]);
  html += kvCard('Identity', idRows);
  html += partsCard(ownRow);
  html += renderActions(detail.actions);
  html += detail.checks.map(checkCard).join('');
  if (detail.faults.length) {
    html += kvCard('Faults', detail.faults.map((f) => [f.code, `${pill(f.severity)} ${esc(f.status)}${f.active ? ' · active' : ''} · ${f.occurrence_count}× · ${esc(f.message)}`]), { wide: true });
  }
  html += `<div class="card wide"><h3>Events · this row and below</h3>${detail.events.map(eventRow).join('') || '<div class="muted">No events.</div>'}</div>`;
  return `${html}</div>`;
}

function renderActions(actions) {
  if (!actions || !actions.length) return '';
  const buttons = actions.map((a) => `<button class="abtn ${a.danger} ${a.state !== 'ready' ? 'blocked' : ''}" data-action="${esc(a.name)}" title="${esc(a.description)}">`
    + `${a.state !== 'ready' ? '<span class="lockdot"></span>' : ''}${esc(a.title)}</button>`).join('');
  const blocked = actions.find((a) => a.state === 'blocked') || actions.find((a) => a.reason);
  return `<div class="card wide"><h3>Actions<span class="right muted">registered by ${esc(actions[0].owner)} · the owner runs them, the host only shows them</span></h3>`
    + `<div class="actions">${buttons}</div>`
    + `<div class="caption">${blocked ? `◌ ${esc(blocked.reason)} · ` : ''}Every action opens a confirm with its preconditions; v1 never executes one.</div></div>`;
}

// --------------------------------------------------------------------------- chip + modal ----
function renderChip() {
  const chip = S.data.chip;
  const worst = S.data.summary.level || 'ok';
  $('chip').className = `chip lvl-${worst} ${S.chip ? 'open' : ''}`;
  $('chip-label').textContent = chip.label;
  const pop = $('chip-pop');
  pop.hidden = !S.chip;
  if (!S.chip) return;
  pop.innerHTML = `<div class="pop-head">${esc(chip.menu[0].text)}</div>`
    + S.data.summary.attention.map((a) => `<div class="pop-row" data-go="${esc(a.path)}"><span class="pop-st">${shape(a.level)}</span><span class="x"><b>${esc(a.device && a.device.path !== a.path ? `${a.device.name} › ${a.name}` : a.name)}</b> — ${esc(a.message)}</span></div>`).join('')
    + '<h4 style="margin-top:10px">Recent</h4>'
    + S.data.recent.slice(0, 5).map((e) => `<div class="pop-row" data-tab="activity"><span class="t">${esc(e.time.slice(0, 5))}</span><span class="x">${esc(e.message)}</span></div>`).join('')
    + '<button class="pop-open" data-close="1">Open IO diagnostics</button>'
    + '<div class="pop-foot">The tray renders this same model (/api/v1/state → chip) through V2\'s AppIndicator.</div>';
}

function renderModal() {
  const back = $('modal');
  const action = S.dialog && S.node && (S.node.actions || []).find((a) => a.name === S.dialog.name);
  back.hidden = !action;
  if (!action) return;
  const pre = action.preconditions.map((p) => `<li><span class="st ${p.state}">${{ pass: '✓', fail: '✗', unknown: '?' }[p.state]}</span><span>${esc(p.text)}</span><span class="why">${esc(p.detail)}</span></li>`).join('');
  back.innerHTML = `<div class="modal" role="dialog"><header><h2>${esc(action.title.replace('…', ''))} · ${esc(S.node.node.name)}</h2><span class="danger ${action.danger}">${esc(action.danger)}</span></header>`
    + `<div class="body"><div class="muted">${esc(action.description)}</div><div class="confirm">${esc(action.confirm)}</div>`
    + `<div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-top:10px">Preconditions · checked passively by the host</div><ul class="pre">${pre}</ul>`
    + (action.reason ? `<div class="banner blocked" style="margin:6px 0 4px">◌ <span><b>${esc(action.reason)}</b></span></div>` : '')
    + `</div><footer><span class="note">v1 never executes: ${esc(action.owner)} would run it as the port's only owner.</span>`
    + `<button class="btn" data-cancel="1">Cancel</button><button class="btn primary ${action.danger}" disabled>${esc(action.title.replace('…', ''))}</button></footer></div>`;
}

function renderStatusbar() {
  const since = (performance.now() - S.lastOk) / 1000;
  const message = S.lastError ? `Host unreachable · last update ${since.toFixed(0)} s ago` : `Last update ${since < 1.5 ? '0' : since.toFixed(0)} s ago`;
  $('lastmsg').textContent = message;
  $('lastmsg').className = S.lastError ? 'late' : '';
  const renderer = renderers()[S.variant];
  if (renderer) $('variant-switch').title = `Tree proposal ${S.variant} · ${renderer.name}${renderer.description ? ` — ${renderer.description}` : ''}`;
}

// --------------------------------------------------------------------------------- render ----
function render() {
  if (!S.data) return;
  const byPath = index(S.data.tree);
  S.built = IOView.build(S.data, S.structure, { ...structureOptions(), query: S.query });
  $('clock').textContent = S.data.clock.iso ? `${S.data.clock.iso.slice(5, 10)}  ${S.data.clock.time}` : '—';
  const fixture = S.data.fixture;
  $('mode').textContent = fixture ? `Replay ${fixture.name} · ${fixture.label} (recorded)` : `Live · ${S.data.host.name}`;
  $('story').textContent = fixture ? fixture.story : `${S.data.summary.devices} devices · ${S.data.host.sources.filter((s) => s.state === 'used').map((s) => s.name).join(', ')}`;
  const skipped = S.data.host.sources.filter((s) => s.state === 'skipped');
  $('sources').innerHTML = skipped.map((s) => `<span class="tag simulated" title="${esc(s.note)}">${esc(s.name)} skipped</span>`).join('');
  document.querySelectorAll('#views button').forEach((b) => b.classList.toggle('on', b.dataset.view === S.view));
  const treeView = S.view === 'tree';
  $('main').className = `main v-${S.view}${treeView && WIDE.has(S.variant) ? ' wide-tree' : ''}`;
  const legacy = treeView && isLegacy(S.variant);
  $('structure-switch').classList.toggle('dim', legacy);
  $('structure-switch').title = legacy ? `${S.variant} is a legacy style: it always draws the physical API tree` : 'Structure: what the rows are';
  $('tree-host').hidden = !treeView;
  $('left-other').hidden = treeView;
  if (treeView) renderTreePane();
  else $('left-other').innerHTML = S.view === 'table' ? renderTable() : renderActivity();
  const noSearch = treeView && NO_QUERY.has(S.variant);
  $('search').disabled = noSearch;
  $('search').placeholder = noSearch ? `No search in ${S.variant} · try Table` : 'Search entities, ids, ports…';
  $('search').closest('.search').classList.toggle('off', noSearch);
  $('detail').innerHTML = renderDetail(byPath);
  renderChip();
  renderModal();
  renderStatusbar();
}

function select(path) {
  S.sel = path;
  S.selKey = null;
  S.node = null;
  loadNode();
}

// ------------------------------------------------------------------------------- events ----
document.addEventListener('click', (event) => {
  const t = event.target;
  // a tree proposal handles its own clicks (selection reaches the page through onSelect); act only on the chip
  if (t.closest('.tree-pane')) { if (S.chip) { S.chip = false; render(); } return; }
  const toggle = t.closest('[data-toggle]');
  if (toggle) {
    const row = S.built && S.built.byKey.get(toggle.dataset.toggle);
    if (row) S.open.set(row.key, !isOpen(row));
    event.stopPropagation();
    return render();
  }
  const mode = t.closest('#mode-switch [data-mode]');
  if (mode) return setMode(mode.dataset.mode);
  if (t.closest('#controls')) return; // the cluster's selects and check chips report through change events
  const view = t.closest('[data-view]');
  if (view) { S.view = view.dataset.view; return render(); }
  if (t.closest('#chip')) { S.chip = !S.chip; return render(); }
  const go = t.closest('[data-go]');
  if (go) { select(go.dataset.go); S.chip = false; if (S.view === 'activity') S.view = 'tree'; return render(); }
  if (t.closest('[data-tab]')) { S.view = t.closest('[data-tab]').dataset.tab; S.chip = false; return render(); }
  if (t.closest('[data-close]')) { S.chip = false; return render(); }
  const action = t.closest('[data-action]');
  if (action) { S.dialog = { name: action.dataset.action }; return render(); }
  if (t.closest('[data-cancel]') || t.id === 'modal') { S.dialog = null; return render(); }
  const row = t.closest('[data-key]');
  if (row) { if (S.view === 'activity') S.view = 'tree'; onTreeSelect(row.dataset.key); return render(); }
  if (S.chip && !t.closest('#chip-pop')) { S.chip = false; render(); }
});
$('search').addEventListener('input', (event) => { S.query = event.target.value; render(); });
$('structure-toggles').addEventListener('change', (event) => {
  const box = event.target.closest('[data-opt]');
  if (box) setToggle(box.dataset.opt, box.checked);
});
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { S.dialog = null; S.chip = false; render(); } });

async function boot() {
  // index.html's inline script already set data-skin / data-mode from storage, so the first paint is right
  const [skins, structures] = await Promise.all([readManifest('skins/index.json', 'skins'), readManifest('view/structure/index.json', 'structures')]);
  S.skins = skins.entries.length ? skins.entries : [{ id: DEFAULTS.skin, name: 'Claude', file: 'claude.css' }];
  await Promise.all([
    ...S.skins.map((skin) => loadStylesheet(`skins/${skin.file}`, skin.id)),
    ...structures.entries.map((entry) => loadScript(`view/structure/${entry.file}`)),
  ]);
  setupSkins(skins.default);
  setupStructures(structures.default);
  setupSwitcher();
  tick();
  setInterval(tick, 1000);
}
boot();
