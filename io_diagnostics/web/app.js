// IO diagnostics v1 page: renders what the host computes (docs/api-v1.md is the only contract it relies on).
// Levels, staleness, rollup and knock-on are decided server-side (contract/record.py via io_diagnostics/tree.py);
// this file lays the same tree out three ways and never talks to hardware. Started from V6's page.
//
// The Tree view is drawn by one of five tree proposals (web/tree/v1..v5.js, window.IOTreeRenderers), picked live
// with the drop-down at the bottom-right and remembered in this browser. The page owns the data, the selection,
// the detail panel, the table and the activity tab; a renderer owns its own rows, expansion, keys and clicks.
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
  open: new Map(),     // path -> true/false, for the table and the built-in fallback tree
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

// ---------------------------------------------------------------------------------- data ----
async function getJson(url) {
  const response = await fetch(url);
  const data = await response.json();
  if (data.error) throw new Error(data.error);
  return data;
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

// ------------------------------------------------------------------------- tree proposals ----
// Each renderer: window.IOTreeRenderers[id] = {name, description?, render(container, apiTree, {onSelect, selectedPath,
// query})}. render() is idempotent, so the page calls it on every 1 s tick with the same persistent pane.
const VARIANT_KEY = 'io-diagnostics.tree-variant';
const DEFAULT_VARIANT = 'v1';
// v5 draws whatever tree it is given and has no search of its own: the page says so instead of a box that does nothing.
const NO_QUERY = new Set(['v5']);
// v5 is the tree that is also the table: its ID / last-seen columns appear from 600 px and all of them from 840 px.
const WIDE = new Set(['v5']);
const scrollTops = new Map();  // variant -> the tree pane's scroll position, restored on switching back

const renderers = () => window.IOTreeRenderers || {};
const variantIds = () => Object.keys(renderers()).filter((id) => typeof renderers()[id].render === 'function').sort();

function storedVariant() {
  try { return localStorage.getItem(VARIANT_KEY); } catch (_) { return null; } // blocked storage: default each load
}
function storeVariant(id) {
  try { localStorage.setItem(VARIANT_KEY, id); } catch (_) { /* private mode: the choice lasts this page only */ }
}

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

function onTreeSelect(path) {
  if (!path || path === S.sel) return;
  select(path);
  render();
}

function renderTreePane() {
  const id = S.variant;
  const renderer = id && renderers()[id];
  const host = $('tree-host');
  if (!renderer) { host.innerHTML = renderTree(); return; } // no proposal loaded: the page's own plain list
  for (const element of host.children) element.hidden = element.dataset.variant !== id;
  const pane = paneFor(id);
  pane.hidden = false;
  try {
    renderer.render(pane, S.data.tree, { onSelect: onTreeSelect, selectedPath: S.sel, query: NO_QUERY.has(id) ? '' : S.query });
  } catch (error) {
    console.error(`tree ${id}:`, error);
    pane.innerHTML = `<div class="err">tree ${esc(id)} failed: ${esc(error)}</div>`;
  }
}

function setupSwitcher() {
  const ids = variantIds();
  const stored = storedVariant();
  S.variant = ids.includes(stored) ? stored : ids.includes(DEFAULT_VARIANT) ? DEFAULT_VARIANT : ids[0] || null;
  const picker = $('tree-variant');
  picker.innerHTML = ids.map((id) => `<option value="${esc(id)}" title="${esc(renderers()[id].description || '')}">`
    + `${esc(id)} · ${esc(renderers()[id].name || id)}</option>`).join('');
  picker.value = S.variant || '';
  $('variant-switch').hidden = ids.length < 2;
  picker.addEventListener('change', () => switchVariant(picker.value));
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
function defaultOpen(node) {
  if (['host', 'group'].includes(node.kind)) return true;
  if (node.kind === 'bus' && node.depth <= 1) return node.id_text !== '0 devices';
  if (node.kind === 'port') return true;
  // open the way to anything that needs attention; keep quiet branches closed
  return Boolean(node.hint) || (node.level && node.level !== 'ok' && node.kind !== 'device');
}
const isOpen = (node) => (S.open.has(node.path) ? S.open.get(node.path) : defaultOpen(node));

function matches(node, q) {
  return [node.name, node.id_text, node.path, node.message, node.hardware_id].some((v) => String(v || '').toLowerCase().includes(q));
}

function visibleRows(tree) {
  const q = S.query.trim().toLowerCase();
  const rows = [];
  const keep = new Set();
  if (q) {
    (function mark(node, trail) {
      if (matches(node, q)) { trail.forEach((p) => keep.add(p)); keep.add(node.path); }
      node.children.forEach((child) => mark(child, trail.concat(node.path)));
    })(tree, []);
  }
  (function walk(node) {
    if (q && !keep.has(node.path)) return;
    rows.push({ node, hit: q && matches(node, q) });
    if (node.children.length && (q ? true : isOpen(node))) node.children.forEach(walk);
  })(tree);
  return rows;
}

// The row's OWN problem (black-box rule): a grouping row has no self-report, so it never draws a shape of its own.
const ownProblem = (node) => (!isGrouping(node) && PROBLEM.has(node.level) ? node.level : null);

function rowHtml(node, hit, inTable) {
  const chev = node.children.length ? `<svg class="chev ${isOpen(node) || S.query ? 'open' : ''}" data-toggle="${esc(node.path)}"><use href="#i-chev"/></svg>`
    : '<svg class="chev none"></svg>';
  const cls = ['row', node.connected ? '' : 'off', node.knock_on_of ? 'knock' : '', hit ? 'hit' : '', !inTable && node.path === S.sel ? 'sel' : ''].join(' ');
  const edge = node.edge === 'part_of' ? '<span class="edge" title="part of its parent (composition)">part</span>' : '';
  // in the table the status and "below" have their own columns; the fallback tree puts its shape at the far right
  const status = inTable ? '' : `<span class="st${node.knock_on_of ? ' knock' : ''}">${shape(ownProblem(node), node.message)}</span>`;
  return `<div class="${cls}" data-path="${esc(node.path)}" style="--d:${node.depth}" title="${esc(node.message)}">${chev}${icon(node.icon)}`
    + `<span class="name">${esc(node.name)}</span>${edge}<span class="cnt" title="${esc(node.id_text)}">${esc(node.id_text)}</span>${status}</div>`;
}

// Fallback only: drawn when no tree proposal registered (e.g. a tree/*.js failed to load).
function renderTree() {
  const counts = S.data.summary.counts;
  const text = ['error', 'stale', 'unknown', 'warn'].filter((l) => counts[l]).map((l) => `${counts[l]} ${l}`).join(' · ') || 'nothing needs you';
  return `<div class="tree-head"><span>Entities · ${S.data.summary.entities}</span><span>${text}</span></div>`
    + visibleRows(S.data.tree).map(({ node, hit }) => rowHtml(node, hit, false)).join('');
}

// ------------------------------------------------------------------------------ table mode --
function fmtSeen(node) {
  if (node.age_s === null || node.age_s === undefined) return '<span class="muted">—</span>';
  const period = node.expected_period_s ? ` / ${node.expected_period_s} s` : '';
  return `<span title="last received ${esc(node.last_received)}">${ago(node.age_s)} ago${period}</span> ${node.stale ? shape('stale', 'stale') : ''}`;
}

// Status column: the row's own self-report, as a shape and a word; nothing when OK (rule 4).
function statusCell(node) {
  const own = ownProblem(node);
  if (!own) return '';
  return `<span class="lvcell${node.knock_on_of ? ' knock' : ''}">${shape(own, node.message)}<span class="lvword lv-${own}">${esc(own)}</span></span>`;
}

// Below column: the advisory hint (a worse cause below), or a grouping row's rollup; quiet text, small shape.
function belowCell(node) {
  const level = node.hint ? node.hint.level : isGrouping(node) && PROBLEM.has(node.level) ? node.level : null;
  if (!level) return '';
  return `<span class="belowcell">${shape(level, `below: ${level}`)}<span>${esc(node.rollup.origin_name || '')}</span></span>`;
}

function renderTable() {
  const rows = visibleRows(S.data.tree);
  const body = rows.map(({ node, hit }) => `<tr class="${node.path === S.sel ? 'sel' : ''} ${node.connected ? '' : 'off'}" data-path="${esc(node.path)}">`
    + `<td class="namecell">${rowHtml(node, hit, true)}</td>`
    + `<td>${statusCell(node)}</td>`
    + `<td title="${esc(node.message)}" class="${ownProblem(node) && !node.knock_on_of ? '' : 'quiet'}">${esc(node.message)}</td>`
    + `<td title="${esc(node.hint ? node.hint.message : '')}">${belowCell(node)}</td>`
    + `<td class="num">${fmtSeen(node)}</td>`
    + `<td class="num">${node.checks || ''}</td>`
    + `<td class="num">${node.faults || ''}</td></tr>`).join('');
  return `<table class="grid"><colgroup><col style="width:330px"><col style="width:96px"><col><col style="width:200px"><col style="width:150px"><col style="width:60px"><col style="width:60px"></colgroup>`
    + '<thead><tr><th>Name · id</th><th>Status</th><th>Message</th><th>Below</th><th>Last seen</th><th>Checks</th><th>Faults</th></tr></thead>'
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

function renderDetail(byPath) {
  const tnode = byPath.get(S.sel) || S.data.tree;
  const detail = S.node && S.node.node.path === tnode.path ? S.node : null;
  const node = tnode;
  const hint = node.hint ? `<span class="muted"> · below: <a class="link" data-go="${esc(node.hint.origin)}">${esc(node.rollup.origin_name)}</a> is ${esc(node.hint.level)}</span>` : '';
  let html = `<div class="d-head"><div class="d-title">${icon(node.icon)}<span>${esc(node.name)}</span>${pill(node.level)}</div>`
    + `<div class="d-msg">${esc(node.message || '—')}${hint}</div>`
    + `<div class="d-path">${esc(node.path)}${node.hardware_id ? ` · ${esc(node.hardware_id)}` : ''}</div></div>`;
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
  $('clock').textContent = S.data.clock.iso ? `${S.data.clock.iso.slice(5, 10)}  ${S.data.clock.time}` : '—';
  const fixture = S.data.fixture;
  $('mode').textContent = fixture ? `Replay ${fixture.name} · ${fixture.label} (recorded)` : `Live · ${S.data.host.name}`;
  $('story').textContent = fixture ? fixture.story : `${S.data.summary.devices} devices · ${S.data.host.sources.filter((s) => s.state === 'used').map((s) => s.name).join(', ')}`;
  const skipped = S.data.host.sources.filter((s) => s.state === 'skipped');
  $('sources').innerHTML = skipped.map((s) => `<span class="tag simulated" title="${esc(s.note)}">${esc(s.name)} skipped</span>`).join('');
  document.querySelectorAll('#views button').forEach((b) => b.classList.toggle('on', b.dataset.view === S.view));
  const treeView = S.view === 'tree';
  $('main').className = `main v-${S.view}${treeView && WIDE.has(S.variant) ? ' wide-tree' : ''}`;
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
    const path = toggle.dataset.toggle;
    S.open.set(path, !isOpen(index(S.data.tree).get(path)));
    event.stopPropagation();
    return render();
  }
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
  const row = t.closest('[data-path]');
  if (row) { select(row.dataset.path); if (S.view === 'activity') S.view = 'tree'; return render(); }
  if (S.chip && !t.closest('#chip-pop')) { S.chip = false; render(); }
});
$('search').addEventListener('input', (event) => { S.query = event.target.value; render(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { S.dialog = null; S.chip = false; render(); } });

setupSwitcher();
tick();
setInterval(tick, 1000);
