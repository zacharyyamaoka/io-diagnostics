// IO diagnostics V6: renders what dashboard.py computes. Staleness and roll-up are decided server-side
// (tree.py); this file only lays the same tree out three ways and never talks to hardware.
'use strict';

const params = new URLSearchParams(location.search);
const S = {
  frame: params.get('frame') || 'F1',
  view: params.get('view') || 'tree',
  sel: params.get('sel'),
  query: '',
  speed: Number(params.get('speed') || 1),
  offset: Number(params.get('t') || 0),
  start: performance.now(),
  data: null,
  act: null,
  actAt: 0,
  open: new Map(),           // path -> true/false, overrides the default expansion
  dialog: params.get('dialog') ? { name: params.get('dialog') } : null,
  chip: params.get('chip') === '1',
  lastOk: 0,
};
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const icon = (name, cls = 'ic') => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
const tag = (t) => t ? `<span class="tag ${esc(t)}">${esc(t)}</span>` : '';
const pill = (level) => `<span class="pill ${level}">${level}</span>`;
const elapsed = () => S.offset + (performance.now() - S.start) / 1000 * S.speed;

// ---------------------------------------------------------------------------------- data ----
async function tick() {
  const t = elapsed().toFixed(1);
  try {
    const response = await fetch(`/api/state?frame=${S.frame}&t=${t}`);
    const data = await response.json();
    if (data.error) throw new Error(data.error);
    S.data = data;
    S.lastOk = performance.now();
    if (!S.sel) S.sel = (data.chip.attention[0] || {}).path || data.tree.path;
    if (performance.now() - S.actAt > 4000 || !S.act || S.act.frame !== S.frame) {
      S.actAt = performance.now();
      fetch(`/api/activity?frame=${S.frame}&t=${t}`).then((r) => r.json()).then((act) => { if (!act.error) { S.act = act; render(); } });
    }
    render();
  } catch (error) {
    $('left').innerHTML = `<div class="err">${esc(error)}</div>`;
  }
}

function index(tree) {
  const byPath = new Map();
  const parent = new Map();
  (function visit(node, up) {
    byPath.set(node.path, node);
    if (up) parent.set(node.path, up);
    node.children.forEach((child) => visit(child, node));
  })(tree, null);
  return { byPath, parent };
}

// ------------------------------------------------------------------------------- tree rows --
function defaultOpen(node) {
  if (['host', 'port', 'bus'].includes(node.kind)) return true;
  if (node.kind === 'device') return node.level !== 'ok' || node.icon === 'hub';
  if (node.kind === 'node') return node.level !== 'ok' && node.level !== 'stale';
  return false;
}
const isOpen = (node) => (S.open.has(node.path) ? S.open.get(node.path) : defaultOpen(node));

function matches(node, q) {
  return [node.name, node.count, node.path, node.message, node.hardware_id].some((v) => String(v || '').toLowerCase().includes(q));
}

function visibleRows(tree) {
  const q = S.query.trim().toLowerCase();
  const rows = [];
  const keep = new Set();
  if (q) {
    (function mark(node, trail) {
      const hit = matches(node, q);
      if (hit) { trail.forEach((p) => keep.add(p)); keep.add(node.path); }
      node.children.forEach((child) => mark(child, trail.concat(node.path)));
    })(tree, []);
  }
  (function walk(node) {
    if (q && !keep.has(node.path)) return;
    const hit = q && matches(node, q);
    rows.push({ node, hit });
    if (node.children.length && (q ? true : isOpen(node))) node.children.forEach(walk);
  })(tree);
  return rows;
}

function rowHtml(node, hit, inTable) {
  const chev = node.children.length ? `<svg class="chev ${isOpen(node) || S.query ? 'open' : ''}" data-toggle="${esc(node.path)}"><use href="#i-chev"/></svg>`
    : '<svg class="chev none"></svg>';
  const cls = ['row', node.os_only ? 'os' : '', hit ? 'hit' : '', !inTable && node.path === S.sel ? 'sel' : ''].join(' ');
  const count = `<span class="cnt" title="${esc(node.count)}">${esc(node.count)}</span>`;
  return `<div class="${cls}" data-path="${esc(node.path)}" style="--d:${node.depth}" title="${esc(node.message)}">${chev}${icon(node.icon)}`
    + `<span class="dot ${node.level}"></span><span class="name">${esc(node.name)}</span>${count}</div>`;
}

function renderTree() {
  const rows = visibleRows(S.data.tree);
  const counts = S.data.tree.counts;
  return `<div class="tree-head"><span>Entities · ${esc(S.data.tree.count)}</span>`
    + `<span>${counts.error} error · ${counts.warn} warn · ${counts.stale} stale</span></div>`
    + rows.map(({ node, hit }) => rowHtml(node, hit, false)).join('');
}

// ------------------------------------------------------------------------------ table mode --
function fmtRate(rate) {
  if (!rate) return '<span class="muted">—</span>';
  const low = rate.measured_hz < rate.expected_hz * 0.95;
  // WHY periods below 1 Hz: rack_inventory polls every 3 min, and "0.0054 / 0.0056 Hz" reads as noise
  const text = rate.expected_hz >= 1
    ? `${rate.measured_hz.toFixed(1)} / ${rate.expected_hz.toFixed(0)} Hz`
    : `1/${Math.round(1 / rate.measured_hz)} s / 1/${Math.round(1 / rate.expected_hz)} s`;
  return `<span class="${low ? 'lagging' : ''}" title="${esc(rate.source)}">${text}</span> ${rate.tag === 'simulated' ? '<span class="tag simulated">sim</span>' : ''}`;
}
function fmtSeen(node) {
  if (node.age_s === null || node.age_s === undefined) return node.kind === 'part' ? '<span class="muted">in parent\'s record</span>' : '<span class="muted" title="presence is event-driven (sysfs + udev): never stale">on event</span>';
  const s = node.age_s;
  const pad = (n) => String(Math.floor(n)).padStart(2, '0');
  const short = s < 90 ? `${Math.round(s)} s` : s < 3600 ? `${Math.floor(s / 60)}m${pad(s % 60)}s` : `${Math.floor(s / 3600)}h${pad(s % 3600 / 60)}m`;
  return `<span title="${esc(node.last_seen)}">${short} ago</span> ${node.stale ? '<span class="pill stale">stale</span>' : ''}`;
}
function fmtFirmware(node) {
  if (!node.firmware) return '<span class="muted">—</span>';
  const v = node.versions || {};
  const sim = v.tag === 'simulated' ? ' class="simv" title="simulated: camera_info is a gated read, not run this round"' : '';
  if (node.update) return `<span${sim}>${esc(node.firmware)}</span> <span class="upd">⬆ ${esc(v.latest)}</span>`;
  return `<span${sim}>${esc(node.firmware)}</span> ${node.update === false ? '<span class="cur">✓</span>' : ''}`;
}

function renderTable() {
  const rows = visibleRows(S.data.tree);
  const body = rows.map(({ node, hit }) => `<tr class="${node.path === S.sel ? 'sel' : ''}" data-path="${esc(node.path)}">`
    + `<td class="namecell">${rowHtml(node, hit, true)}</td>`
    + `<td>${pill(node.level)}</td>`
    + `<td title="${esc(node.message)}">${esc(node.message)}</td>`
    + `<td class="num">${fmtRate(node.rate)}</td>`
    + `<td class="num">${fmtSeen(node)}</td>`
    + `<td class="num">${fmtFirmware(node)}</td>`
    + `<td class="num" title="${esc(node.errors)}">${esc(node.errors || '—')}</td></tr>`).join('');
  return `<table class="grid"><colgroup><col style="width:330px"><col style="width:76px"><col><col style="width:150px"><col style="width:168px"><col style="width:160px"><col style="width:112px"></colgroup>`
    + '<thead><tr><th>Name · id</th><th>Status</th><th>Message</th><th>Rate (meas / exp)</th><th>Last seen</th><th>Firmware</th><th>Errors</th></tr></thead>'
    + `<tbody>${body}</tbody></table>`;
}

// --------------------------------------------------------------------------- activity tab --
function stripHtml(cells, marks, cls = '') {
  const set = new Set(marks || []);
  return `<div class="strip ${cls}">${cells.map((c, i) => `<i class="${c}${set.has(i) ? ' mark' : ''}"></i>`).join('')}</div>`;
}
function axis(clock) {
  const end = new Date(clock);
  const at = (minutes) => new Date(end.getTime() - minutes * 60000).toTimeString().slice(0, 5);
  return `<div class="axis"><span>${at(59)}</span><span>${at(45)}</span><span>${at(30)}</span><span>${at(15)}</span><span>${at(0)} now</span></div>`;
}
function eventRow(event) {
  return `<div class="ev" data-device="${esc(event.device)}" data-port="${esc(event.port)}"><span class="t">${esc(event.time)}</span>`
    + `<span><span class="kind ${esc(event.kind.replace(/ /g, '-'))}">${esc(event.kind)}</span></span><span class="p">${esc(event.port)}</span>`
    + `<span>${esc(event.text)}</span>${tag(event.tag)}</div>`;
}
function renderActivity() {
  const act = S.act;
  if (!act) return '<div class="err muted">loading activity…</div>';
  const strips = act.strips.map((s) => `<div class="sname ${s.os_only ? 'os' : ''}" data-path="${esc(s.path)}" title="${esc(s.path)}">${esc(s.name)}</div>${stripHtml(s.cells, s.marks)}`).join('');
  const lh = act.link_health.map((r) => `<tr data-path="${esc(r.path)}"><td title="${esc(r.edge)}">${esc(r.edge)}</td><td>${esc(r.kind)}</td><td>${esc(r.metric)}</td>`
    + `<td title="${esc(r.value)}"><span class="dot ${r.level}"></span> ${esc(r.value)}</td><td>${tag(r.tag)}</td></tr>`).join('');
  return `<div class="act"><section><h2>Activity log <span class="muted" style="text-transform:none;letter-spacing:0;font-weight:400">· ${act.events.length} events, newest first · time and port on every entry</span></h2>`
    + act.events.map(eventRow).join('') + '</section>'
    + `<section><h2>Timeline · last 60 min, one cell per minute</h2><div class="strips">${strips}<div></div>${axis(act.clock)}</div>`
    + '<div class="caption">Each cell is the device\'s rolled-up level at the end of that minute, from the same tree; dark = not plugged in; a white tick = a USB (re-)enumeration in that minute.</div>'
    + `<h2 style="margin-top:22px">Link health · the computer diagnoses its own wires</h2><table class="lh"><colgroup><col style="width:30%"><col style="width:84px"><col style="width:20%"><col><col style="width:80px"></colgroup>`
    + `<thead><tr><th>Edge</th><th>Kind</th><th>Metric</th><th>Value</th><th>Source</th></tr></thead><tbody>${lh}</tbody></table></section></div>`;
}

// --------------------------------------------------------------------------------- detail ----
function kvCard(title, rows, opts = {}) {
  const body = rows.filter(Boolean).map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`).join('');
  return `<div class="card ${opts.wide ? 'wide' : ''}"><h3>${opts.icon ? icon(opts.icon) : ''}${esc(title)}${opts.right ? `<span class="right">${opts.right}</span>` : ''}</h3><div class="kv">${body}</div></div>`;
}
function tableCard(title, rows, opts = {}) {
  if (!rows || !rows.length) return '';
  const body = rows.map((r) => `<tr><td class="lv"><span class="dot ${r[2] || 'ok'}"></span></td><td class="k">${esc(r[0])}</td><td>${esc(r[1])}</td><td class="s">${esc(r[3] || '')} ${tag(r[4])}</td></tr>`).join('');
  return `<div class="card ${opts.wide ? 'wide' : ''}"><h3>${esc(title)}${opts.right ? `<span class="right">${opts.right}</span>` : ''}</h3><table class="vt">${body}</table></div>`;
}

function renderDetail(idx) {
  const node = idx.byPath.get(S.sel) || S.data.tree;
  const up = idx.parent.get(node.path);
  const origin = node.origin !== node.path ? idx.byPath.get(node.origin) : null;
  const ownNote = origin ? `<span class="muted"> · own ${esc(node.own_level)} · worst below: <a class="link" data-go="${esc(origin.path)}">${esc(origin.name)}</a></span>` : '';
  let html = `<div class="d-head"><div class="d-title">${icon(node.icon)}<span>${esc(node.name)}</span>${pill(node.level)}${tag(node.tag)}</div>`
    + `<div class="d-msg">${esc(node.message || '—')}${ownNote}</div>`
    + `<div class="d-path">${esc(node.path)}${node.hardware_id ? ` · ${esc(node.hardware_id)}` : ''}</div></div>`;
  if (node.blocked) html += `<div class="banner blocked">◌ <span><b>Checks and actions wait:</b> ${esc(node.blocked)}. Last known values stay visible with their age; nothing here competes for the port.</span></div>`;
  else if (up && up.blocked && node.kind !== 'device') html += `<div class="banner blocked">◌ <span><b>Waits on its adapter:</b> ${esc(up.blocked)}.</span></div>`;
  if (node.os_only) html += '<div class="banner osonly">No driver diagnostics for this device: its driver has not registered a <span class="mono">diagnostics.py</span>. Shown from the OS alone: presence, port, VID:PID, serial, link speed.</div>';
  html += '<div class="cards">';
  const status = [
    ['level', `${pill(node.level)} <span class="muted">reported ${esc(node.reported_level)}</span>`],
    ['message', esc(node.own_message || '—')],
    ['last seen', node.age_s === null ? `<span class="muted">${node.kind === 'part' ? 'values inside its parent\'s record' : 'presence is event-driven (sysfs + udev): never stale'}</span>` : `${esc(node.last_seen?.slice(11))} · ${esc(node.age)} ago ${node.stale ? pill('stale') : ''}`],
    node.expected_period_s ? ['expected period', `${node.expected_period_s} s · STALE after ${node.stale_after_s} s (3×)`] : null,
    node.rate ? ['rate', `${fmtRate(node.rate)} <span class="muted">${esc(node.rate.source)}</span>`] : null,
    node.children.length ? ['below', `${node.counts.error} error · ${node.counts.warn} warn · ${node.counts.stale} stale · ${node.counts.ok} ok`] : null,
    ['source', `${esc(node.source)}`],
  ];
  html += kvCard('Status', status);
  const id = node.identity || {};
  if (Object.keys(id).length) {
    const via = up ? `<a class="link" data-go="${esc(up.path)}">${esc(id.via_device || up.name)}</a>` : esc(id.via_device || '—');
    const known = ['manufacturer', 'model', 'serial_number', 'hw_version', 'sw_version'];
    const rows = known.map((k) => [k.replace('_', ' '), esc(id[k] ?? '—')]);
    rows.push(['via device', via]);
    Object.entries(id).filter(([k]) => !known.includes(k) && k !== 'via_device').forEach(([k, v]) => rows.push([k.replace('_', ' '), esc(v)]));
    html += kvCard('Identity · device_info', rows);
  }
  const v = node.versions || {};
  if (Object.keys(v).length) {
    const badge = v.update === true ? '<span class="update-badge">update available</span>' : v.update === false ? '<span class="current-badge">current</span>' : '';
    const rows = [];
    if (v.installed && v.installed !== '—') rows.push(['installed', `${esc(v.installed)}${badge} ${v.tag ? tag(v.tag) : ''}<div class="muted" style="font-size:11px">${esc(v.installed_source || '')}</div>`]);
    if (v.latest && v.latest !== '—') rows.push(['latest', `${esc(v.latest)}<div class="muted" style="font-size:11px">${esc(v.latest_source || '')}</div>`]);
    if (v.git) rows.push(['git', `<span class="mono">${esc(v.git)}</span>`]);
    if (v.boards_latest) rows.push(['boards latest', `${esc(v.boards_latest)} <span class="muted">${esc(v.boards_latest_source || '')}</span>`]);
    if (v.driver) rows.push(['driver', `${esc(v.driver)} <span class="muted">${esc(v.driver_source || '')}</span>`]);
    if (rows.length) html += kvCard('Versions', rows);
  }
  html += renderActions(node);
  html += tableCard('Comms', node.comms);
  if (node.config && node.config.length) {
    const body = node.config.map((r) => `<tr><td class="k">${esc(r[0])}</td><td class="mono">${esc(r[1])}</td></tr>`).join('');
    const sources = [...new Set(node.config.map((r) => r[2]))].join(' · ');
    html += `<div class="card"><h3>${icon('lock')}Config · read-only</h3><table class="vt">${body}</table><div class="caption">${esc(sources)}</div></div>`;
  }
  html += tableCard('Values', node.values, { wide: false });
  html += renderHistory(node, idx);
  html += '</div>';
  return html;
}

function renderActions(node) {
  if (node.os_only) return '<div class="card wide"><h3>Actions</h3><div class="muted">None: no driver registered actions for this device.</div></div>';
  if (!node.actions || !node.actions.length) return '';
  const buttons = node.actions.map((a) => `<button class="abtn ${a.danger} ${a.blocked ? 'blocked' : ''}" data-action="${esc(a.name)}" title="${esc(a.description)}">`
    + `${a.blocked ? '<span class="lockdot"></span>' : ''}${esc(a.title)}</button>`).join('');
  const blocked = node.actions.find((a) => a.blocked.startsWith('blocked')) || node.actions.find((a) => a.blocked);
  return `<div class="card wide"><h3>Actions<span class="right muted">registered by ${esc(node.actions[0].driver)} · the driver runs them, the dashboard only asks</span></h3>`
    + `<div class="actions">${buttons}</div>`
    + `<div class="caption">${blocked ? `◌ ${esc(blocked.blocked)} · ` : ''}Every action opens a confirm with its preconditions; this prototype never executes one.</div></div>`;
}

function renderHistory(node, idx) {
  if (!S.act) return '';
  let device = node;
  while (device && device.kind !== 'device' && idx.parent.get(device.path)) device = idx.parent.get(device.path);
  if (!device || device.kind !== 'device') return '';
  const strip = S.act.strips.find((s) => s.path === device.path);
  const port = device.path.split('/').slice(-2, -1)[0].replace(/^usb-|^port-/, '');
  const serial = device.path.split('-').pop();
  const events = S.act.events.filter((e) => e.port === port || e.device.startsWith(serial)).slice(0, 6);
  return `<div class="card wide"><h3>History · ${esc(device.name)}<span class="right muted">last 60 min · port ${esc(port)}</span></h3>`
    + (strip ? stripHtml(strip.cells, strip.marks) + axis(S.act.clock) : '')
    + `<div style="margin-top:8px">${events.map(eventRow).join('') || '<div class="muted">No events for this device today.</div>'}</div></div>`;
}

// --------------------------------------------------------------------------- chip + modal ----
function renderChip(idx) {
  const chip = S.data.chip;
  const worst = chip.attention.reduce((w, a) => (['ok', 'warn', 'stale', 'error'].indexOf(a.level) > ['ok', 'warn', 'stale', 'error'].indexOf(w) ? a.level : w), 'ok');
  $('chip').className = `chip lvl-${worst} ${S.chip ? 'open' : ''}`;
  $('chip-label').textContent = chip.label;
  const pop = $('chip-pop');
  pop.hidden = !S.chip;
  if (!S.chip) return;
  pop.innerHTML = `<div class="pop-head">${esc(chip.menu[0].text)}</div>`
    + chip.attention.map((a) => `<div class="pop-row" data-go="${esc(a.path)}"><span class="dot ${a.level}" style="margin-top:4px"></span><span class="x"><b>${esc(a.name)}</b> — ${esc(a.message)}</span></div>`).join('')
    + '<h4 style="margin-top:10px">Recent</h4>'
    + S.data.recent.slice(0, 5).map((e) => `<div class="pop-row" data-tab="activity"><span class="t">${esc(e.time.slice(0, 5))}</span><span class="x">${esc(e.text)}</span></div>`).join('')
    + '<button class="pop-open" data-close="1">Open IO diagnostics</button>'
    + '<div class="pop-foot">The tray build renders this same model through V2\'s real AppIndicator (contract/indicator.py): label, Recent, devices that need you, Open.</div>';
}

function renderModal(idx) {
  const back = $('modal');
  const node = idx.byPath.get(S.sel);
  const action = S.dialog && node && (node.actions || []).find((a) => a.name === S.dialog.name);
  back.hidden = !action;
  if (!action) return;
  const pre = action.preconditions.map((p) => `<li><span class="st ${p.state}">${{ pass: '✓', fail: '✗', unknown: '?' }[p.state]}</span><span>${esc(p.text)}</span><span class="why">${esc(p.detail)}</span></li>`).join('');
  back.innerHTML = `<div class="modal" role="dialog"><header><h2>${esc(action.title.replace('…', ''))} · ${esc(node.name)}</h2><span class="danger ${action.danger}">${esc(action.danger)}</span></header>`
    + `<div class="body"><div class="muted">${esc(action.description)}</div><div class="confirm">${esc(action.confirm)}</div>`
    + `<div class="muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.5px;margin-top:10px">Preconditions · checked passively, re-checked by the driver before it sends</div><ul class="pre">${pre}</ul>`
    + (action.blocked ? `<div class="banner blocked" style="margin:6px 0 4px">◌ <span><b>${esc(action.blocked)}</b> — the button stays disabled until the holder lets go.</span></div>` : '')
    + `</div><footer><span class="note">Placeholder: this prototype never executes. In the product ${esc(action.driver)} runs it as the port's only owner.</span>`
    + `<button class="btn" data-cancel="1">Cancel</button><button class="btn primary ${action.danger}" disabled>${esc(action.title.replace('…', ''))}</button></footer></div>`;
}

// --------------------------------------------------------------------------------- render ----
function render() {
  if (!S.data) return;
  const idx = index(S.data.tree);
  const clock = new Date(S.data.clock);
  $('clock').textContent = `Oct ${clock.getDate()}  ${S.data.clock.slice(11, 19)}`;
  const frame = S.data.frames.find((f) => f.id === S.frame);
  $('story').textContent = frame ? frame.story : '';
  $('frames').innerHTML = S.data.frames.map((f) => `<button data-frame="${f.id}" class="${f.id === S.frame ? 'on' : ''}">${esc(f.label)}</button>`).join('');
  document.querySelectorAll('#views button').forEach((b) => b.classList.toggle('on', b.dataset.view === S.view));
  document.querySelectorAll('#speeds button').forEach((b) => b.classList.toggle('on', Number(b.dataset.speed) === S.speed));
  $('main').className = `main v-${S.view}`;
  const left = $('left');
  left.innerHTML = S.view === 'tree' ? renderTree() : S.view === 'table' ? renderTable() : renderActivity();
  $('detail').innerHTML = renderDetail(idx);
  renderChip(idx);
  renderModal(idx);
  const ago = (performance.now() - S.lastOk) / 1000;
  $('lastmsg').textContent = `Last update ${ago < 1.5 ? '0' : ago.toFixed(0)} s ago · ${S.speed}×`;
  if (S.act) $('host-strip').innerHTML = stripHtml(S.act.strips[0].cells, [], 'mini').replace(/^<div class="strip mini">|<\/div>$/g, '');
}

// ------------------------------------------------------------------------------- events ----
document.addEventListener('click', (event) => {
  const t = event.target;
  const toggle = t.closest('[data-toggle]');
  if (toggle) {
    const path = toggle.dataset.toggle;
    const node = index(S.data.tree).byPath.get(path);
    S.open.set(path, !isOpen(node));
    event.stopPropagation();
    return render();
  }
  const frame = t.closest('[data-frame]');
  if (frame) { S.frame = frame.dataset.frame; S.offset = 0; S.start = performance.now(); S.act = null; S.sel = null; S.dialog = null; return tick(); }
  const view = t.closest('[data-view]');
  if (view) { S.view = view.dataset.view; return render(); }
  const speed = t.closest('[data-speed]');
  if (speed) { S.offset = elapsed(); S.start = performance.now(); S.speed = Number(speed.dataset.speed); return render(); }
  if (t.closest('#restart')) { S.offset = 0; S.start = performance.now(); return tick(); }
  if (t.closest('#chip')) { S.chip = !S.chip; return render(); }
  const go = t.closest('[data-go]');
  if (go) { S.sel = go.dataset.go; S.chip = false; if (S.view === 'activity') S.view = 'tree'; return render(); }
  if (t.closest('[data-tab]')) { S.view = t.closest('[data-tab]').dataset.tab; S.chip = false; return render(); }
  if (t.closest('[data-close]')) { S.chip = false; return render(); }
  const action = t.closest('[data-action]');
  if (action) { S.dialog = { name: action.dataset.action }; return render(); }
  if (t.closest('[data-cancel]') || t.id === 'modal') { S.dialog = null; return render(); }
  const row = t.closest('[data-path]');
  if (row) { S.sel = row.dataset.path; if (S.view === 'activity') S.view = 'tree'; return render(); }
  const ev = t.closest('.ev');
  if (ev && S.data) {
    const idx = index(S.data.tree);
    const hit = [...idx.byPath.values()].find((n) => n.kind === 'device' && (n.path.includes(`usb-${ev.dataset.port}/`) || n.path.includes(`port-${ev.dataset.port}/`)))
      || [...idx.byPath.values()].find((n) => n.kind === 'port' && n.name === `Port ${ev.dataset.port}`);
    if (hit) { S.sel = hit.path; S.view = 'tree'; render(); }
  }
  if (S.chip && !t.closest('#chip-pop')) { S.chip = false; render(); }
});
$('search').addEventListener('input', (event) => { S.query = event.target.value; render(); });
document.addEventListener('keydown', (event) => { if (event.key === 'Escape') { S.dialog = null; S.chip = false; render(); } });

tick();
setInterval(tick, 1000);
