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
  util: null,          // the utility being dry-run / confirmed / run: {path, name, node, phase, dry, result, ...}
  modalHtml: '',
  why: new Map(),      // entity path -> {checks: [{name, level, message, lines, evidence}], message, at}: what_to_check
  attOpen: new Set(),  // attention items whose checklist the viewer opened (the first one is open unless closed)
  attClosed: new Set(),
  attHtml: '',
  detailHtml: '',
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

// WHY p1: Zach's "fdcanusb → Moteus c1 · CAN 15" (entities, transport as columns, a Ports toggle one click away)
const DEFAULTS = { skin: 'claude', structure: 'p1', variant: 'v6' };
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
  // a toggle with `when` shows only while it means something (P2's Empty ports: Connections only)
  $('structure-toggles').innerHTML = (structure.toggles || []).filter((toggle) => IOView.toggleShown(toggle, options)).map((toggle) => `<label class="ctl-check" title="${esc(toggle.title || toggle.label)}">`
    + `<input type="checkbox" data-opt="${esc(toggle.key)}"${options[toggle.key] ? ' checked' : ''}>${esc(toggle.label)}</label>`).join('');
}
function setToggle(key, value) {
  S.toggles[S.structure] = { ...(S.toggles[S.structure] || {}), [key]: value };
  writePref(PREFS.toggles, JSON.stringify(S.toggles));
  renderToggles();
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
  // the row seams (docs/round2-seams.md §3): lead note, then muted attrs / suffix that are not the id
  const attrs = (row.attrs || []).filter(Boolean);
  const lead = row.lead ? `<span class="lead">${esc(row.lead)}</span>` : '';
  const extra = (attrs.length ? `<span class="attrs">${esc(attrs.join(' · '))}</span>` : '') + (row.suffix ? `<span class="suffix">${esc(row.suffix)}</span>` : '');
  return `<div class="${cls}" data-key="${esc(row.key)}" style="--d:${row.depth}" title="${esc(row.title || row.label)}">${chev}${icon(row.icon)}`
    + `<span class="name">${esc(row.label)}</span>${lead}${note}${extra}<span class="fill"></span>${statusMark(row)}</div>`;
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

// WHY a minimum: the Message column is the one that says what is wrong; with the detail pane open at 1600 px and a
// structure's extra columns, a fixed layout gave it what was left (Zach's screenshot: "ME"). The table now grows
// past the pane and scrolls sideways instead.
const MESSAGE_MIN_PX = 280;
const FIXED_COLUMNS = [['Name', 380], ['ID', 170], ['Message', null], ['Below', 190]];
const TAIL_COLUMNS = [['Last seen', 140], ['Checks', 60], ['Faults', 60]];
function renderTable() {
  const rows = visibleRows(S.built.rows);
  const extra = S.built.columns || [];
  const span = FIXED_COLUMNS.length + extra.length + TAIL_COLUMNS.length;
  const body = rows.map((row) => {
    const selected = row.key === selectedKey();
    const tr = `<tr class="${selected ? 'sel' : ''} ${row.connected === false ? 'off' : ''}" data-key="${esc(row.key)}">`
      + `<td class="namecell">${rowHtml(row, false)}</td>`
      + `<td class="mono idcell" title="${esc(row.id_text || '')}">${esc(row.idText || '')}</td>`
      + messageCell(row)
      + belowCell(row)
      + extra.map((column) => `<td class="${column.mono ? 'mono xcol' : 'xcol'}" title="${esc(row.columns[column.key] || '')}">${esc(row.columns[column.key] || '')}</td>`).join('')
      + `<td class="num">${fmtSeen(row)}</td>`
      + `<td class="num">${row.checks || ''}</td>`
      + `<td class="num">${row.faults || ''}</td></tr>`;
    // the subtitle (P3's breadcrumb): a muted second line under the selected row, as in the tree
    return selected && row.subtitle ? `${tr}<tr class="subrow"><td colspan="${span}"><div class="sub" style="--d:${row.depth}">${esc(row.subtitle)}</div></td></tr>` : tr;
  }).join('');
  const widths = (columns) => columns.map(([, width]) => (width ? `<col style="width:${width}px">` : '<col>')).join('');
  const extraCols = extra.map((column) => `<col style="width:${esc(column.width || '160px')}">`).join('');
  const extraHeads = extra.map((column) => `<th>${esc(column.title)}</th>`).join('');
  const fixed = [...FIXED_COLUMNS, ...TAIL_COLUMNS].reduce((sum, [, width]) => sum + (width || 0), 0)
    + extra.reduce((sum, column) => sum + (parseInt(column.width, 10) || 160), 0);
  return `<table class="grid" style="min-width:${fixed + MESSAGE_MIN_PX}px"><colgroup>${widths(FIXED_COLUMNS)}${extraCols}${widths(TAIL_COLUMNS)}</colgroup>`
    + `<thead><tr><th>Name</th><th>ID</th><th class="msgcol">Message</th><th>Below</th>${extraHeads}<th>Last seen</th><th>Checks</th><th>Faults</th></tr></thead>`
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
// A structure marks the transport layers it folded in (folded: 'path'): those are the row's connection, not its parts.
function partsCard(row) {
  if (!row || !row.parts || !row.parts.length) return '';
  const item = (part) => `<div class="part-row" data-go="${esc(part.entity)}" title="${esc(part.name)}">${icon(part.icon)}`
    + `<span class="pname">${esc(part.label)}</span><span class="pmsg">${esc(part.message)}</span>`
    + `<span class="st${part.knock_on_of ? ' knock' : ''}">${shape(part.problem, part.message)}</span></div>`;
  const path = row.parts.filter((part) => part.folded === 'path');
  const parts = row.parts.filter((part) => part.folded !== 'path');
  return (path.length ? `<div class="card wide"><h3>Connection (folded) · ${path.length}<span class="right muted">the layers this row is reached through, folded into it</span></h3>${path.map(item).join('')}</div>` : '')
    + (parts.length ? `<div class="card wide"><h3>Parts · ${parts.length}<span class="right muted">composition (part_of), folded into this row</span></h3>${parts.map(item).join('')}</div>` : '');
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
  html += whatToCheckCard(detail, ownRow);
  // the utilities right under the problem: what to check, then the buttons that check or fix it (printer style)
  html += renderUtilities(detail.actions);
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
  html += detail.checks.map(checkCard).join('');
  if (detail.faults.length) {
    html += kvCard('Faults', detail.faults.map((f) => [f.code, `${pill(f.severity)} ${esc(f.status)}${f.active ? ' · active' : ''} · ${f.occurrence_count}× · ${esc(f.message)}`]), { wide: true });
  }
  html += `<div class="card wide"><h3>Events · this row and below</h3>${detail.events.map(eventRow).join('') || '<div class="muted">No events.</div>'}</div>`;
  return `${html}</div>`;
}

// ------------------------------------------------------------------ utilities (the action buttons) ----
// The printer-utility window (Zach, 2026-10-22: Canon "Cleaning": icon, title, one-line description per utility),
// over docs/actions-vocabulary-v0.1.md. Checks first (query), then maintenance (changes / interrupts), then motion
// (moves) set apart, then the driver's own non-standard actions under "More".
// WHY every click is a dry run first: the person reads exactly what would run (argv, steps, preconditions, the
// plugin's own confirm text) before anything can; a query then runs with one more click, anything else only from a
// confirm modal that repeats the danger (moves: a ticked box that states the physical precondition). The server
// runs a real action only with the dry run's token, from this page (io_diagnostics/server.py, docs/api-v1.md).
const STANDARD_ORDER = ['check_comms', 'check_firmware', 'update_firmware', 'update_config', 'clear_faults', 'set_id', 'reboot',
  'stop_all', 'open_console', 'calibrate'];
const STANDARD = new Set(STANDARD_ORDER);
const DANGER_WORDS = {
  query: 'Reads only.',
  interrupts: 'Interrupts what runs: a stream drops, a board restarts or stops.',
  changes: 'Changes what the device stores: its configuration, id, faults or firmware.',
  moves: 'Makes hardware move: a motor spins, and a loaded joint can fall.',
};
// a precondition only a person can check (the host cannot read it passively): what the moves box states
const PHYSICAL = new Set(['joint_free', 'target_in_view']);
const RUNNABLE = new Set(['ready', 'unknown']); // blocked / nothing_to_do keep the Run button disabled
const UTIL_ICONS = {
  check_comms: '<path d="M4.9 19.1a10 10 0 0 1 0-14.2M7.8 16.2a6 6 0 0 1 0-8.4M16.2 7.8a6 6 0 0 1 0 8.4M19.1 4.9a10 10 0 0 1 0 14.2"/><circle cx="12" cy="12" r="2"/>',
  check_firmware: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  update_firmware: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
  update_config: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  clear_faults: '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
  set_id: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
  reboot: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  stop_all: '<path d="M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9Z"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
  open_console: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3M11 15h6"/>',
  calibrate: '<circle cx="12" cy="12" r="8"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/><circle cx="12" cy="12" r="1.5"/>',
  more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/><rect x="2" y="5" width="20" height="14" rx="3"/>',
};
const utilIcon = (name) => `<svg class="u-ic" viewBox="0 0 24 24" aria-hidden="true">${UTIL_ICONS[STANDARD.has(name) ? name : 'more']}</svg>`;
const plainTitle = (title) => String(title || '').replace(/…$/, '');
const hhmmss = (ms) => new Date(ms).toTimeString().slice(0, 8);

function utilityGroups(actions) {
  const order = (action) => (STANDARD.has(action.name) ? STANDARD_ORDER.indexOf(action.name) : STANDARD_ORDER.length);
  const sorted = [...actions].sort((a, b) => order(a) - order(b));
  const standard = sorted.filter((action) => STANDARD.has(action.name));
  return [
    { id: 'checks', title: 'Checks', items: standard.filter((action) => action.danger === 'query') },
    { id: 'maintenance', title: 'Maintenance', items: standard.filter((action) => action.danger !== 'query' && action.danger !== 'moves') },
    { id: 'motion', title: 'Motion · makes hardware move', items: standard.filter((action) => action.danger === 'moves') },
    { id: 'more', title: 'More', items: sorted.filter((action) => !STANDARD.has(action.name)) },
  ].filter((group) => group.items.length);
}

// The CAN id a node-scoped action names: the entity's own last path segment ("…/can/15" -> 15), as the host's {id}.
function nodeOf(path, action) {
  if (action.scope !== 'node') return null;
  const last = String(path).split('/').pop();
  return /^\d+$/.test(last) ? Number(last) : null;
}

async function postAct(body) {
  try {
    const response = await fetch('/api/v1/act', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify(body) });
    return { status: response.status, data: await response.json() };
  } catch (error) {
    return { status: 0, data: { refused: 'Unreachable', error: `the host did not answer: ${error}` } };
  }
}

async function startDryRun(name) {
  const action = S.node && (S.node.actions || []).find((item) => item.name === name);
  if (!action) return;
  const path = S.node.node.path;
  const util = { path, name, node: nodeOf(path, action), phase: 'dry', dry: null, result: null, error: '', refused: '', ticked: false };
  S.util = util;
  render();
  const { status, data } = await postAct({ path, action: name, node: util.node, dry_run: true });
  if (S.util !== util) return;
  if (status === 200) { util.dry = data; util.at = Date.now(); } else { util.refused = data.refused || `HTTP ${status}`; util.error = data.error || ''; }
  util.phase = 'shown';
  render();
}

async function runUtility() {
  const util = S.util;
  if (!util || !util.dry || !util.dry.token || util.phase === 'running') return;
  util.phase = 'running';
  util.startedAt = Date.now();
  render();
  const { status, data } = await postAct({ path: util.path, action: util.name, node: util.node, token: util.dry.token });
  if (S.util !== util) return;
  util.phase = 'done';
  if (status === 200) util.result = data.result; else { util.refused = data.refused || `HTTP ${status}`; util.error = data.error || ''; }
  S.nodeAt = 0; // refresh the node now: the action may have changed what it reports
  loadNode();
  render();
}

// The host's passive evaluation (pass / fail / unknown + detail) beside the plugin's own reading, by check name.
function preconditionRows(action, answer) {
  const plugin = new Map(((answer && answer.preconditions) || []).map((item) => [item.check, item]));
  const rows = (action.preconditions || []).map((item) => ({ check: item.check, text: item.text, state: item.state, detail: item.detail,
    plugin: plugin.has(item.check) ? plugin.get(item.check).state : '' }));
  for (const [check, item] of plugin) if (!rows.some((row) => row.check === check)) rows.push({ check, text: item.text, state: 'unknown', detail: '', plugin: item.state });
  return rows;
}
function preconditionsHtml(rows) {
  if (!rows.length) return '<div class="muted">No preconditions.</div>';
  return `<ul class="pre">${rows.map((row) => `<li><span class="st ${esc(row.state)}">${{ pass: '✓', fail: '✗' }[row.state] || '?'}</span>`
    + `<span>${esc(row.text)}</span><span class="why">${esc([row.detail, row.plugin ? `driver: ${row.plugin}` : ''].filter(Boolean).join(' · '))}</span></li>`).join('')}</ul>`;
}

const commandOf = (answer) => {
  const command = answer && answer.command;
  if (Array.isArray(command) && command.length) return command.join(' ');
  if (typeof command === 'string' && command) return command;
  return '';
};

function stepsHtml(answer) {
  const steps = (answer && answer.steps) || [];
  if (!steps.length) return '';
  return `<ol class="u-steps">${steps.map((step) => `<li><span>${esc(step.purpose)}</span> <span class="tag recorded">${esc(step.runs || '')}</span>`
    + `${step.argv && step.argv.length ? `<code>${esc(step.argv.join(' '))}</code>` : ''}`
    + `${step.sends && step.sends.length ? `<span class="muted"> · sends ${esc(step.sends.join(', '))}</span>` : ''}</li>`).join('')}</ol>`;
}

const FAILED_CODES = new Set(['ActionRefused', 'ConfirmationRequired', 'ConfirmRequired', 'ActionFailed']);
function resultHtml(util) {
  if (!util.result) {
    return `<div class="u-result fail"><div class="u-result-h">${shape('error', 'not run')}<b>Not run</b> · ${esc(util.refused)}</div><div>${esc(util.error)}</div></div>`;
  }
  const result = util.result;
  const records = result.records || [];
  const refusedByDriver = records.find((record) => FAILED_CODES.has(record.code));
  const ok = !result.refused && result.outcome === 'ok' && !refusedByDriver;
  const head = ok ? `<b>Done</b> · ${esc(result.message || `${result.outcome} in ${result.duration_s} s`)}`
    : `${shape('error', 'failed')}<b>${esc(result.refused ? `Refused: ${result.refused}` : refusedByDriver ? `${refusedByDriver.code}` : result.outcome)}</b> · ${esc(result.message || (refusedByDriver && refusedByDriver.message) || '')}`;
  const lines = records.map((record) => `<li><span class="mono">${esc(record.code || record.record || '')}</span> ${esc(record.message || record.name || '')}</li>`).join('');
  return `<div class="u-result ${ok ? 'ok' : 'fail'}"><div class="u-result-h">${head}</div>`
    + `${lines ? `<ul class="u-records">${lines}</ul>` : ''}`
    + `${(result.rejected || []).length ? `<pre class="u-tail">${esc(result.rejected.join('\n'))}</pre>` : ''}`
    + `${result.stderr_tail ? `<pre class="u-tail">${esc(result.stderr_tail)}</pre>` : ''}`
    + `<div class="muted">${esc(result.plugin || '')} · ${esc(result.outcome)} · ${esc(result.duration_s)} s · exit ${esc(result.returncode)}</div></div>`;
}

function dryRunPanel(action) {
  const util = S.util;
  const close = '<button class="btn" data-util-close>Close</button>';
  if (util.phase === 'dry') return `<div class="u-panel"><div class="muted">Dry run · asking ${esc(action.owner)} what it would run…</div></div>`;
  if (!util.dry) return `<div class="u-panel">${resultHtml(util)}<div class="u-foot">${close}</div></div>`;
  const result = util.dry.result || {};
  const answer = result.action_result || {};
  if (result.refused || result.outcome !== 'ok' || !util.dry.token) {
    return `<div class="u-panel"><div class="u-result fail"><div class="u-result-h">${shape('error', 'dry run failed')}<b>The dry run did not answer</b> · ${esc(result.refused || result.outcome)}</div>`
      + `<div>${esc(result.message || '')}</div>${result.stderr_tail ? `<pre class="u-tail">${esc(result.stderr_tail)}</pre>` : ''}</div><div class="u-foot">${close}</div></div>`;
  }
  const command = commandOf(answer);
  const confirm = answer.confirm || action.confirm;
  const runnable = RUNNABLE.has(action.state);
  // the token's window runs on this computer's clock (a replay's clock shows the recorded time)
  const expires = `${Math.round((util.dry.window_s || 300) / 60)} min`;
  let foot;
  if (util.phase === 'running') foot = `<span class="u-running">Running since ${esc(hhmmss(util.startedAt))}…</span>`;
  else if (util.phase === 'done') foot = `<button class="btn" data-util-again>Dry run again</button>${close}`;
  else if (action.danger === 'query') foot = `<button class="btn primary query" data-util-run${runnable ? '' : ' disabled'}>Run ${esc(plainTitle(action.title))}</button>${close}`;
  else foot = `<button class="btn primary ${esc(action.danger)}" data-util-run${runnable ? '' : ' disabled'}>Continue…</button>${close}`;
  return `<div class="u-panel">`
    + `<div class="u-panel-h">Dry run · nothing ran<span class="right muted">its token is good for ${esc(expires)}</span></div>`
    + `<div class="u-k">Will run</div><div class="u-v"><code class="u-cmd">${esc(command || 'in-process (no command line)')}</code>`
    + `${answer.command_kind ? ` <span class="tag recorded">${esc(answer.command_kind)}</span>` : ''}</div>`
    + `${stepsHtml(answer) ? `<div class="u-k">Steps</div><div class="u-v">${stepsHtml(answer)}</div>` : ''}`
    + `<div class="u-k">Preconditions</div><div class="u-v">${preconditionsHtml(preconditionRows(action, answer))}</div>`
    + `${confirm ? `<div class="u-k">Confirm</div><div class="u-v"><div class="confirm">${esc(confirm)}</div></div>` : ''}`
    + `${!runnable ? `<div class="banner blocked">◌ <span><b>${esc(action.reason || action.state)}</b></span></div>` : ''}`
    + `<div class="u-k">Through</div><div class="u-v"><code class="u-argv">${esc((result.argv || []).join(' '))}</code></div>`
    + `${util.phase === 'done' ? resultHtml(util) : ''}`
    + `<div class="u-foot">${foot}</div></div>`;
}

function utilityRow(action) {
  const active = S.util && S.util.path === S.sel && S.util.name === action.name;
  const runnable = RUNNABLE.has(action.state);
  const reason = action.state !== 'ready' && action.reason ? `<span class="u-reason">${esc(action.reason)}</span>` : '';
  const danger = action.danger !== 'query' ? `<span class="danger ${esc(action.danger)}">${esc(action.danger)}</span>` : '';
  return `<div class="urow${active ? ' active' : ''}${runnable ? '' : ' blocked'}" data-util-row="${esc(action.name)}">`
    + `<button class="u-btn" data-util="${esc(action.name)}" title="Dry run: show what ${esc(plainTitle(action.title))} would run">`
    + `${utilIcon(action.name)}<span class="u-text"><span class="u-title">${esc(action.title)}</span>`
    + `<span class="u-desc">${esc(action.description)}</span>${reason}</span>${danger}</button>`
    + `${active ? dryRunPanel(action) : ''}</div>`;
}

function renderUtilities(actions) {
  if (!actions || !actions.length) return '';
  const groups = utilityGroups(actions).map((group) => `<div class="ugroup ugroup-${group.id}"><div class="ugroup-h">${esc(group.title)}</div>`
    + `${group.items.map(utilityRow).join('')}</div>`).join('');
  return `<div class="card wide utilities"><h3>Utilities<span class="right muted">${esc(actions[0].owner)} runs them · every click is a dry run first</span></h3>${groups}</div>`;
}

// The confirm modal for anything but a query: the danger repeated in words, the plugin's confirm text, what runs,
// the preconditions; for moves a box stating the physical precondition must be ticked, and the button says the action.
function renderModal() {
  const back = $('modal');
  const util = S.util;
  const action = util && util.phase === 'confirm' && S.node && util.path === S.node.node.path && (S.node.actions || []).find((item) => item.name === util.name);
  if (!action || !util.dry) {
    back.hidden = true;
    if (S.modalHtml) { back.innerHTML = ''; S.modalHtml = ''; }
    return;
  }
  back.hidden = false;
  const answer = (util.dry.result || {}).action_result || {};
  const rows = preconditionRows(action, answer);
  const moves = action.danger === 'moves';
  const physical = rows.find((row) => PHYSICAL.has(row.check)) || rows.find((row) => /person confirms/.test(row.plugin || ''));
  const tick = physical ? physical.text : (answer.confirm || action.confirm);
  const label = (S.built && S.built.byEntity.has(util.path) && S.built.byKey.get(S.built.byEntity.get(util.path)).entity === util.path)
    ? S.built.byKey.get(S.built.byEntity.get(util.path)).label : S.node.node.name;
  const html = `<div class="modal m-${esc(action.danger)}" role="dialog" aria-modal="true" aria-labelledby="m-title">`
    + `<header><h2 id="m-title">${esc(plainTitle(action.title))} · ${esc(label)}</h2><span class="danger ${esc(action.danger)}">${esc(action.danger)}</span></header>`
    + `<div class="body"><div class="m-danger">${shape(moves ? 'error' : 'warn', action.danger)}<b>${esc(DANGER_WORDS[action.danger] || 'Needs your confirmation: its danger class is not one this page knows.')}</b></div>`
    + `<div class="confirm">${esc(answer.confirm || action.confirm)}</div>`
    + `<div class="m-runs">Runs <code>${esc(commandOf(answer) || 'in-process')}</code></div>`
    + `<div class="m-pre-h">Preconditions</div>${preconditionsHtml(rows)}`
    + `${moves ? `<label class="m-tick"><input type="checkbox" data-util-tick${util.ticked ? ' checked' : ''}><span>I checked: ${esc(tick)}</span></label>` : ''}`
    + `</div><footer><span class="note">${esc(action.owner)} runs it once you press the button; the dry run's token is good for ${esc(Math.round((util.dry.window_s || 300) / 60))} min.</span>`
    + `<button class="btn" data-util-cancel>Cancel</button>`
    + `<button class="btn primary ${esc(action.danger)}" data-util-confirm${moves && !util.ticked ? ' disabled' : ''}>${esc(plainTitle(action.title))}</button></footer></div>`;
  if (html !== S.modalHtml) { back.innerHTML = html; S.modalHtml = html; }
}

// ------------------------------------------------------------------- what to check (plain words) ----
// A status whose cause has a checklist carries the value `what_to_check` (numbered lines, newline-separated, in the
// order to check them; moteus_servo docs/io_diagnostics_actions.md, the host's ExpectedAbsent rows). The page's top
// attention line and the detail panel show the plain-words reason and those lines, as authored.
// WHY at the top: when boards don't answer, "motor supply on? XT30 splitter? CAN cable? CAN ground?" is the thing
// Zach needs first (Daily Note 2026-10-22: "I plug it in and now I need it to run").
function whyOf(detail) {
  return ((detail && detail.checks) || []).filter((check) => PROBLEM.has(check.effective_level)).map((check) => {
    const values = Object.fromEntries((check.values || []).map((value) => [value.key, value.value]));
    if (!values.what_to_check) return null;
    return { name: check.name, level: check.effective_level, message: check.message || '', code: check.code || '',
      lines: String(values.what_to_check).split('\n').filter((line) => line.trim()), evidence: values.kernel_evidence || '' };
  }).filter(Boolean);
}

// /api/v1/node for a path whose checklist the page shows (attention items, the selection's problem parts); cached,
// refreshed when the item's message changes or every 5 s.
function wantWhy(path, message = '') {
  const known = S.why.get(path);
  if (known && (known.loading || (known.message === message && performance.now() - known.at < 5000))) return;
  S.why.set(path, { ...(known || { checks: [] }), loading: true, message, at: performance.now() });
  getJson(`/api/v1/node?path=${encodeURIComponent(path)}`)
    .then((detail) => { S.why.set(path, { checks: whyOf(detail), loading: false, message, at: performance.now() }); render(); })
    .catch(() => { S.why.set(path, { checks: [], loading: false, message, at: performance.now() }); });
}

const whyLines = (why) => `<div class="why-lines">${why.lines.map((line) => `<div>${esc(line)}</div>`).join('')}</div>`
  + (why.evidence ? `<div class="why-ev">kernel evidence: ${esc(why.evidence)}</div>` : '');

// The top attention line: every independent cause the API lists (summary.attention), worst first, each with its
// plain-words reason; the first one's checklist open, the others one click away. Hidden when nothing needs you.
function renderAttention() {
  const element = $('attention');
  const items = S.data.summary.attention || [];
  if (!items.length) { element.hidden = true; element.innerHTML = ''; return; }
  for (const item of items.slice(0, 6)) wantWhy(item.path, item.message);
  const rows = items.slice(0, 4).map((item, at) => {
    const checks = (S.why.get(item.path) || {}).checks || [];
    const why = checks.find((check) => check.message === item.message) || checks[0];
    const open = why && (at === 0 ? !S.attClosed.has(item.path) : S.attOpen.has(item.path));
    const where = item.device && item.device.path !== item.path ? `${item.device.name} › ${item.name}` : item.name;
    const toggle = why ? `<button class="att-more" data-att="${esc(item.path)}" aria-expanded="${Boolean(open)}">${open ? 'hide' : `what to check · ${why.lines.length}`}</button>` : '';
    return `<div class="att-item lv-${esc(item.level)}" data-att-item="${esc(item.path)}"><div class="att-line"><span class="att-st">${shape(item.level, item.level)}</span>`
      + `<a class="att-where" data-go="${esc(item.path)}">${esc(where)}</a><span class="att-msg" title="${esc(item.message)}">${esc(item.message)}</span>${toggle}</div>`
      + `${open ? whyLines(why) : ''}</div>`;
  }).join('');
  const rest = items.length > 4 ? `<div class="att-rest">+${items.length - 4} more · the chip lists every one</div>` : '';
  const html = `${rows}${rest}`;
  element.hidden = false;
  if (html !== S.attHtml) { element.innerHTML = html; S.attHtml = html; }
}

// The detail panel's "What to check": the selection's own checklists, then its folded parts' (the CAN bus folded
// into its adapter), each with the plain-words reason.
function whatToCheckCard(detail, ownRow) {
  const own = whyOf(detail).map((why) => ({ ...why, who: '' }));
  const parts = ((ownRow && ownRow.parts) || []).filter((part) => part.problem).flatMap((part) => {
    wantWhy(part.entity, part.message);
    return ((S.why.get(part.entity) || {}).checks || []).map((why) => ({ ...why, who: part.label, entity: part.entity }));
  });
  const all = [...own, ...parts];
  if (!all.length) return '';
  return `<div class="card wide why"><h3>What to check</h3>${all.map((why) => `<div class="why-item">`
    + `<div class="why-head">${shape(why.level, why.level)}<b>${why.entity ? `<a class="link" data-go="${esc(why.entity)}">${esc(why.who)}</a> · ` : ''}${esc(why.name)}</b></div>`
    + `<div class="why-msg">${esc(why.message)}</div>${whyLines(why)}</div>`).join('')}</div>`;
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
  renderAttention();
  const detailHtml = renderDetail(byPath);
  // WHY compare: the dry-run panel and the What to check list are read, not glanced at; an unchanged detail is not
  // rewritten every second (selection, focus and scroll stay)
  if (detailHtml !== S.detailHtml) { $('detail').innerHTML = detailHtml; S.detailHtml = detailHtml; }
  renderChip();
  renderModal();
  renderStatusbar();
}

function select(path) {
  S.sel = path;
  S.selKey = null;
  S.node = null;
  if (S.util && S.util.path !== path) S.util = null; // a utility belongs to the entity it was opened on
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
  // utilities: a click dry-runs; Run (a query) or Continue… (the confirm modal) only after it
  const util = t.closest('[data-util]');
  if (util) { startDryRun(util.dataset.util); return undefined; }
  if (t.closest('[data-util-again]') && S.util) { startDryRun(S.util.name); return undefined; }
  if (t.closest('[data-util-run]') && S.util) {
    const action = S.node && (S.node.actions || []).find((item) => item.name === S.util.name);
    if (!action) return undefined;
    if (action.danger === 'query') { runUtility(); return undefined; }
    S.util.phase = 'confirm';
    S.util.ticked = false;
    return render();
  }
  if (t.closest('[data-util-confirm]')) {
    const action = S.util && S.node && (S.node.actions || []).find((item) => item.name === S.util.name);
    if (action && (action.danger !== 'moves' || S.util.ticked)) runUtility(); // moves: only with the box ticked
    return undefined;
  }
  if (t.closest('[data-util-cancel]') || t.id === 'modal') { if (S.util && S.util.phase === 'confirm') S.util.phase = 'shown'; return render(); }
  if (t.closest('[data-util-close]')) { S.util = null; return render(); }
  if (t.closest('[data-util-tick]')) return undefined; // its change event records the tick
  const att = t.closest('[data-att]');
  if (att) {
    const key = att.dataset.att;
    const first = ((S.data && S.data.summary.attention) || [])[0];
    if (first && first.path === key) { if (S.attClosed.has(key)) S.attClosed.delete(key); else S.attClosed.add(key); } else if (S.attOpen.has(key)) S.attOpen.delete(key); else S.attOpen.add(key);
    return render();
  }
  const row = t.closest('[data-key]');
  if (row) { if (S.view === 'activity') S.view = 'tree'; onTreeSelect(row.dataset.key); return render(); }
  if (S.chip && !t.closest('#chip-pop')) { S.chip = false; render(); }
});
$('search').addEventListener('input', (event) => { S.query = event.target.value; render(); });
$('structure-toggles').addEventListener('change', (event) => {
  const box = event.target.closest('[data-opt]');
  if (box) setToggle(box.dataset.opt, box.checked);
});
document.addEventListener('change', (event) => {
  if (event.target.closest('[data-util-tick]') && S.util) { S.util.ticked = event.target.checked; render(); }
});
document.addEventListener('keydown', (event) => {
  if (event.key !== 'Escape') return;
  if (S.util && S.util.phase === 'confirm') S.util.phase = 'shown';
  S.chip = false;
  render();
});

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
