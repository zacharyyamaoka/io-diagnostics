/* RobotDx on data-ui: the shell (docs/peps/0001-robotdx-on-data-ui.md §12).

   Zach, 2026-10-26: "reimplement robotdx using data-ui … explore the raw data. AND we also want this ability to make
   dashboards … at any moment you can just kinda like 'rewind' and just go back in time". So: an app rail, one clock
   for the whole page (Rerun's time panel), Explore (the tree and the selected entity, both data-ui views over the live
   datasets of feed.js), Activity, Dashboards (boards.js), Runs and Settings. Every panel is a stock <data-views> or
   <data-dashboard>; this file only routes, binds datasets and keeps the URL.

   Read-only by construction: the only request that is not a GET is watch.js's heartbeat (PEP §12.2). Every write the
   host offers (utilities, configuration, the expected setup, settings) stays on the classic page, linked per entity. */

import { LiveClock, LiveDataset, parseBase, stringifyBase, loadDataset, connectSSE, connectPoll, makeResizable, bindRailSelect, esc, fmtTime, loadView } from '../vendor/data-ui/data-ui.js';
import '../vendor/data-ui/dashboard.js';
import { RobotDxFeed, bindingOf } from './feed.js';
import { startHeartbeat } from './watch.js';
import { mountDashboards } from './boards.js';

// WHY the views' code first, beside the feed's first requests: data-ui's live <data-views> builds its first model before its live
// feed is attached and keeps serving it while the view's module loads, so a live draw in that window drew an empty tree until the
// next change came (on F1 the host's ~2 s tick: first paint 1.5-1.9 s on 7 of 24 cold loads). A module that is ready before the
// first live frame takes the live path at once (PEP 0001 §15.1; asked of data-ui's live lane)
for (const type of ['table', 'kpi', 'playback', 'plot', 'levels']) loadView(type).catch(() => { /* the view says it could not load */ });

const app = document.getElementById('dx-app');
const main = document.getElementById('dx-main');
const rail = document.getElementById('dx-rail');
const timeBar = document.getElementById('dx-time');
const BOARDS = new URL('dashboards/', import.meta.url).href;
const DESTS = ['explore', 'activity', 'dashboards', 'runs', 'settings'];

// ---- the one clock -------------------------------------------------------------------------------------------------------
// WHY on the app element: clockFor() walks up from any <data-views> to the first element with a clock, so every view in
// the page reads this one; each <data-dashboard> is handed it before load() (PEP §12, "One clock for the page")
const clock = new LiveClock({ loop: false });
app.clock = clock;
// WHY moved in place: <data-live-status for="dx-app"> connected when data-ui's module ran (before this line), found no clock on
// #dx-app and looks again only at its 1 s tick; a move re-runs its connect, so the line reads the page clock from the start
const statusLine = document.getElementById('dx-status');
if (statusLine && statusLine.parentNode) statusLine.parentNode.insertBefore(statusLine, statusLine.nextSibling);
const feed = new RobotDxFeed({ clock });
window.robotdx = Object.freeze({ clock, feed, parseBase, stringifyBase, version: 'dx 1 (PEP 0001 §12)', get boards() { return window.__dxBoards || null; } });

// WHY bound again after every dashboard binds: data-ui's <data-dashboard> binds the clock to its own datasets and lets go of
// every other one (dashboard/src/live.js bindLive takes the clock for its own). This page's clock is the page's: the tree, the
// time bar and the other views read the page's datasets, and a view whose dataset the clock let go of shows its newest rows
// whatever t is (the tree did not rewind). So every dataset the page holds goes back on it (PEP 0001 §15.1; asked of data-ui)
function rebind() { for (const ds of Object.values(feed.datasets())) clock.bind(ds); }
document.addEventListener('data-dashboard-live', rebind, true);   // capture: the event does not bubble

// ---- the URL: ?go=<destination>&sel=<path>&board=<id>#t=live|<seconds> -----------------------------------------------------
export function readUrl() {
  const q = new URLSearchParams(location.search);
  const go = DESTS.includes(q.get('go')) ? q.get('go') : 'explore';
  return { go, sel: q.get('sel') || '', board: q.get('board') || '' };
}

/** Go somewhere: a history entry (Back and Forward walk them, E4), the clock's t kept in the hash. */
export function go(dest, params = {}, { replace = false } = {}) {
  const q = new URLSearchParams();
  q.set('go', dest);
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  const url = `${location.pathname}?${q}${tHash()}`;
  if (`${location.pathname}${location.search}` === `${location.pathname}?${q}`) return render();
  history[replace ? 'replaceState' : 'pushState'](null, '', url);
  return render();
}

function tHash() {
  if (!clock.live) return '';
  return clock.following ? '#t=live' : `#t=${clock.t.toFixed(3).replace(/\.?0+$/, '')}`;
}
function writeNow() {
  try {
    const h = new URLSearchParams(location.hash.slice(1));
    h.set('t', clock.following ? 'live' : clock.t.toFixed(3).replace(/\.?0+$/, ''));
    history.replaceState(history.state, '', `${location.pathname}${location.search}#${h.toString().replace(/%2C/gi, ',').replace(/%3A/gi, ':')}`);
  } catch (_) { /* a sandboxed frame: the page still works, unshared */ }
}
// WHY at most four times a second, the last one always written: a scrub seeks at the pointer's rate, and replaceState
// at 60 Hz is a page-wide stall (data-ui's dashboard writes its URL on a pause for the same reason)
let urlTimer = null, urlAt = 0;
function writeT() {
  const wait = 250 - (performance.now() - urlAt);
  if (wait <= 0) { urlAt = performance.now(); writeNow(); return; }
  if (!urlTimer) urlTimer = setTimeout(() => { urlTimer = null; urlAt = performance.now(); writeNow(); }, wait);
}
clock.addEventListener('state', () => writeT());
clock.addEventListener('time', (e) => { if (!clock.following && e.detail.source !== 'play') writeT(); });

// ---- before the page knew: say so, never an empty page that looks like a dead host ------------------------------------------
// WHY: the host keeps checks' levels, State and events for its window, and each tree row's health (GET /history/tree), but not
// the tree's messages, counts or settings at a past time (PEP 0001 §5c4, §15); the page has those from the moment it opened.
// Scrubbed back before that, the tree is today's rows with the health the host kept, and this line says so (on a host without
// /history/tree the tree is empty then, and the line says why).
const before = document.getElementById('dx-before');
let beforeAt = 0;
const binText = (s) => `${+Number(s).toFixed(1)} s`;
function sayBefore() {
  const opened = feed.openedAt;
  const show = !clock.following && Number.isFinite(opened) && Number.isFinite(clock.t) && clock.t < opened;
  if (show) {
    const back = feed.treeBack;
    const from = back && back.supported && Number.isFinite(back.from) ? back.from : null;
    before.textContent = from !== null && clock.t >= from
      ? `Before ${fmtTime(opened)} (when this page opened) the tree is today's rows, each with the worst health the host kept for it in each ${binText(back.bin_s)}; what is below a row, presence, counts and settings start at ${fmtTime(opened)}.`
      : from !== null
        ? `Before ${fmtTime(from)} the host kept no health for the tree (its history starts then); this page opened at ${fmtTime(opened)}.`
        : `Before ${fmtTime(opened)} the page has no tree, counts or settings (it opened then; the host keeps no history of them). Checks, State, health and events go back further.`;
  }
  before.hidden = !show;
}
// WHY the last call always runs: a seek's own `time` often comes within 100 ms of the one before it (following moves t each
// frame), and a throttle that drops it left the line hidden after a scrub ('state' fires before t moves)
let beforeTimer = null;
function sayBeforeSoon() {
  // following, the line is hidden and stays so: no work on every frame's `time` (the profile found a timer a frame here)
  if (clock.following && before.hidden && !beforeTimer) return;
  const wait = 100 - (performance.now() - beforeAt);
  if (wait <= 0) { beforeAt = performance.now(); sayBefore(); return; }
  if (!beforeTimer) beforeTimer = setTimeout(() => { beforeTimer = null; beforeAt = performance.now(); sayBefore(); }, wait);
}
clock.addEventListener('time', sayBeforeSoon);
clock.addEventListener('state', sayBeforeSoon);
feed.addEventListener('tree-back', sayBefore);

// ---- the clock's keys outside a dashboard (a dashboard handles its own: data-ui CONTRACT §18, §21) ------------------------
document.addEventListener('keydown', (e) => {
  const t = e.target;
  if (t && t.closest && t.closest('input, select, textarea, [contenteditable], data-dashboard, dialog, .dv-pb-track, data-views table')) return;
  if (!clock.live) return;
  const mod = e.ctrlKey || e.metaKey;
  const done = () => { e.preventDefault(); e.stopPropagation(); };
  if ((e.key === 'End' && !mod && !e.altKey) || (e.key === 'ArrowRight' && mod && e.shiftKey)) { done(); clock.goLive(); }
  else if ((e.key === 'Home' && !mod) || (e.key === 'ArrowLeft' && mod && e.shiftKey)) { done(); clock.seek(clock.start ?? clock.liveStart); }
  else if (e.key === ' ' && !mod && !e.altKey) { done(); clock.toggle(); }
  else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && !mod && !e.altKey) { done(); const s = e.key === 'ArrowRight' ? 1 : -1; if (e.shiftKey) clock.seek(clock.t + s); else clock.step(s); }
});

// ---- opening a .base with the page's datasets ------------------------------------------------------------------------------

const stem = (p) => String(p).split('?')[0].split('/').pop().replace(/\.[^.]+$/, '');

/** The datasets a .base declares, named as <data-dashboard> names them (PEP 0004: datasets:, and the top-level data:). */
function declared(base) {
  const out = {};
  for (const [name, entry] of Object.entries(base.datasets || {})) {
    const d = typeof entry === 'string' ? { data: entry } : { ...(entry || {}) };
    if (d.data) out[name] = d;
  }
  if (base.data && !Object.values(out).some((d) => d.data === base.data)) out[stem(base.data)] = { data: base.data };
  return out;
}

/** A dataset that is not one of the page's: as data-ui loads it (a file, or a live: SSE / polling / pushed source). */
async function plainDataset(href, live, closers) {
  const file = await loadDataset(href);
  if (!live || typeof live !== 'object') return { ...file, url: href };
  const ds = new LiveDataset({ id: file.id || stem(href), title: file.title, titleProperty: file.titleProperty, properties: file.properties, rows: file.rows,
    url: href, time: live.time || 't', stream: live.stream || null, key: live.key || null, retention: live.retention || {}, stale: live.stale ?? 5, recorded: live.recorded === true });
  if (live.sse) closers.push(connectSSE(ds, new URL(live.sse, href).href, { events: live.events }));
  else if (live.poll) closers.push(connectPoll(ds, new URL(live.poll, href).href, { every: live.every ?? '1s', since: live.since, cursor: live.cursor, rows: live.rows }));
  else if (!live.recorded) ds.setStatus('live');
  clock.bind(ds);
  closers.push({ close: () => clock.unbind(ds) });
  return ds;
}

/**
 * Draw a .base's text in a <data-dashboard>, with the page's own LiveDataset for every binding (PEP §12.1: a data: file
 * of dx/datasets/ is a kind, its ?path= the entity) and data-ui's loading for anything else. Returns {release(), base}.
 * WHY not <data-dashboard src>: src makes new datasets per dashboard, and the history the page holds would start
 * again on every switch; one dataset per kind and entity for the whole page keeps rewind across destinations.
 */
export async function openBase(el, text, baseUrl, { keepState = false } = {}) {
  const base = parseBase(text);
  const datasets = {};
  const used = [];
  const closers = [];
  // WHY only what a panel draws: a binding's polls (and, for State, the device's watch) run for a view on screen, never
  // for a dataset a .base merely declares (PEP §12.2)
  const all = declared(base);
  const viewsByName = new Map((base.views || []).map((v) => [v.name, v]));
  const panels = (base.layout && base.layout.panels) || [];
  const drawn = panels.length ? panels.flatMap((p) => (p.views || (p.view ? [p.view] : [])).map((n) => viewsByName.get(n)).filter(Boolean)) : (base.views || []);
  const fallback = base.data ? Object.keys(all).find((n) => all[n].data === base.data) || stem(base.data) : null;
  const shown = new Set(drawn.map((v) => v.from || fallback).filter(Boolean));
  for (const [name, d] of Object.entries(all)) {
    const href = new URL(d.data, baseUrl).href;
    const b = bindingOf(href);
    if (b) {
      datasets[name] = feed.dataset(b.kind, b.path);
      if (shown.has(name)) { feed.use(b.kind, b.path); used.push(b); }
    } else {
      try { datasets[name] = await plainDataset(href, d.live, closers); } catch (err) { datasets[name] = { properties: {}, rows: [], url: href, error: err.message }; }
    }
  }
  el.clock = clock;
  el.baseText = text;
  el.load(base, datasets, { keepState });
  rebind();   // a dashboard with no live dataset sends no data-dashboard-live, and still let go of the page's
  return {
    base,
    release() { for (const b of used) feed.release(b.kind, b.path); for (const c of closers) c.close(); used.length = 0; closers.length = 0; },
  };
}

/** Whether a drawn dashboard has its own playback card (then the page's time bar steps aside). */
const hasPlayback = (base) => (base.views || []).some((v) => v.type === 'playback');
export function showTimeBar(on) { timeBar.hidden = !on; }

// ---- Explore: the tree and the selected entity -------------------------------------------------------------------------------

const TREE_BASE = {
  views: [{
    type: 'table', name: 'Tree',
    // WHY these filters: an OS device no driver claims (a keyboard, a mouse) stays hidden unless it reports a problem
    // of its own, and a hidden row never hides a fault (E11)
    filters: 'claimed == true || rank > 0',
    tree: { parent: 'parent' }, fold: 'open',
    order: ['name', 'health', 'below', 'id_text', 'model', 'presence'],
    columnSize: { name: 210, health: 84, below: 84 },
  }, {
    type: 'table', name: 'Table',
    filters: 'claimed == true || rank > 0',
    order: ['name', 'health', 'message', 'id_text', 'rate', 'heard', 'checks', 'faults', 'kind'],
    sort: [{ property: 'rank', direction: 'DESC' }],
  }],
};

const FOLD = 'robotdx.dx.tree.folded';
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (_) { /* private window: this visit only */ } },
};

let templateText = null;
async function template(name) {
  const res = await fetch(new URL(name, BOARDS), { cache: 'no-store' });
  return res.text();
}

/** Explore's .base for one entity: the template with {path}, a Signals tab per State value, empty panels left out. */
function exploreText(tpl, path, entity) {
  const base = parseBase(tpl.replaceAll('{path}', path));
  const keys = entity ? [...entity.keys.entries()] : [];
  const numeric = keys.filter(([k]) => !['mode', 'fault'].includes(k)).length ? keys.filter(([k]) => !['mode', 'fault'].includes(k)) : keys;
  const node = entity && entity.node;
  const drop = new Set();
  if (!keys.length) { drop.add('state'); drop.add('signals'); }
  if (!node || !(node.config || []).length) drop.add('configuration');
  base.views = base.views || [];
  for (const [key, info] of numeric) {
    base.views.push({ type: 'plot', name: info.unit ? `${key} (${info.unit})` : key, from: 'samples', at: '30s', plot: { y: ['value'], series: 'key', only: [key], unit: info.unit || undefined } });
  }
  base.layout.panels = base.layout.panels.map((p) => (p.key === 'signals' ? { ...p, views: numeric.map(([key, info]) => (info.unit ? `${key} (${info.unit})` : key)) } : p))
    .filter((p) => !drop.has(p.key || String(p.view || '').toLowerCase()));
  return stringifyBase(base);
}

function withoutPlayback(text) {
  const base = parseBase(text);
  const gone = new Set((base.views || []).filter((v) => v.type === 'playback').map((v) => v.name));
  if (!gone.size) return text;
  base.views = base.views.filter((v) => !gone.has(v.name));
  base.layout.panels = base.layout.panels.filter((p) => !gone.has(p.view));
  return stringifyBase(base);
}

async function mountExplore(root, params) {
  root.innerHTML = `<div class="dx-explore">
    <div class="dx-bar">
      <button type="button" class="dx-btn" id="dx-tree-fold" aria-controls="dx-tree-pane" aria-pressed="false" title="Fold the tree away (and back)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/></svg><span>Tree</span></button>
      <span class="dx-crumbs" id="dx-crumbs"></span><span class="dx-grow"></span>
      <button type="button" class="dx-btn" id="dx-keep" hidden title="Copy this entity's panels into a dashboard of your own">Keep as a dashboard</button>
      <a class="dx-link" id="dx-classic" href="/" title="Utilities, configuration edits and the expected setup are on the classic page">Open on the classic page ↗</a>
    </div>
    <div class="dx-explore-body">
      <aside id="dx-tree-pane" aria-label="Entities"><data-views id="dx-tree" no-url toolbar="compact"></data-views></aside>
      <section class="dx-detail-wrap"><data-dashboard id="dx-detail" no-url></data-dashboard></section>
    </div>
  </div>`;
  const pane = root.querySelector('#dx-tree-pane');
  const tree = root.querySelector('#dx-tree');
  const detail = root.querySelector('#dx-detail');
  const fold = root.querySelector('#dx-tree-fold');
  const crumbs = root.querySelector('#dx-crumbs');
  const classic = root.querySelector('#dx-classic');
  const keep = root.querySelector('#dx-keep');
  // WHY: Zach always wants side panels that drag, remember, reset and fold (memory feedback_resizable_panels)
  const sizer = makeResizable(pane, { edge: 'right', min: 220, max: 640, keep: 420, key: 'dx-tree', label: 'the entity tree' });
  const setFold = (on) => { pane.hidden = on; fold.setAttribute('aria-pressed', String(on)); store.set(FOLD, on ? '1' : null); };
  fold.addEventListener('click', () => setFold(!pane.hidden));
  setFold(store.get(FOLD) === '1');
  tree.base = TREE_BASE;
  tree.data = feed.dataset('entities');
  // a click on a row selects it (the URL and Back follow), never a peek: the entity opens beside the tree
  tree.openPeek = (id) => go('explore', { sel: String(id) });
  const mark = document.createElement('style');
  root.append(mark);

  let current = { path: null, release: null, seq: 0, hold: null, sig: '' };
  // what decides Explore's panels for an entity: its State keys (in order) and whether it has settings
  const sigOf = (entity) => (entity ? `${[...entity.keys.keys()].join(',')}|${Boolean(entity.node && (entity.node.config || []).length)}` : '');
  async function select(path) {
    const seq = ++current.seq;
    mark.textContent = path ? `#dx-tree tr[data-id="${CSS.escape(path)}"] { background: var(--dv-hover); box-shadow: inset 3px 0 0 var(--dv-accent); }` : '';
    classic.href = path ? `/?sel=${encodeURIComponent(path)}` : '/';
    const e = path ? feed.dataset('entities').latestAt(Infinity, path) : null;
    crumbs.innerHTML = path ? `<b>${esc((e && e.name) || path)}</b> <span>${esc(path)}</span>` : 'Pick an entity in the tree';
    keep.hidden = !path;
    let text, url = BOARDS;
    if (path) {
      // the entity's feeds start now; the panels' own bindings take them over (counted), and these let go after. 'peek' stays
      // while the entity is selected: its State and its settings may come after the selection (refresh, below)
      const kinds = ['checks', 'config', 'levels'];
      for (const k of [...kinds, 'peek']) feed.use(k, path);
      templateText = templateText || await template('explore.base');
      const entity = await feed.whenReady(path);
      if (seq !== current.seq) { for (const k of [...kinds, 'peek']) feed.release(k, path); return; }
      text = exploreText(templateText, path, entity);
      const was = current.release;
      current.release = null;
      const opened = await openBase(detail, text, url);
      if (was) was();
      for (const k of kinds) feed.release(k, path);
      const hold = { panels: opened };
      current = { ...current, path, hold, sig: sigOf(entity), release: () => { hold.panels.release(); feed.release('peek', path); }, text };
    } else {
      // nothing picked: the Overview starter, without its own time bar (Explore's is the page's)
      text = withoutPlayback(await template('overview.base'));
      if (seq !== current.seq) return;
      const was = current.release;
      const opened = await openBase(detail, text, url);
      if (was) was();
      current = { ...current, path: null, hold: null, sig: '', release: () => opened.release(), text };
    }
    // WHY hidden where the detail has its own playback card: one time bar on screen, never two
    showTimeBar(!hasPlayback(parseBase(text)));
  }
  // WHY: an entity can have no State or settings yet when it is selected (a device whose first point comes seconds later), and
  // exploreText leaves an empty panel out; when they come, the panels are added without a new selection (the reader's t and
  // tabs kept)
  let refreshing = false;
  async function refresh(e) {
    if (refreshing || !current.path || !current.hold || (e && e.detail && e.detail.path !== current.path)) return;
    const entity = feed.entity(current.path);
    const sig = sigOf(entity);
    if (sig === current.sig) return;
    const text = exploreText(templateText, current.path, entity);
    current.sig = sig;
    if (text === current.text) return;
    refreshing = true;
    const { hold, seq } = current;
    try {
      const opened = await openBase(detail, text, BOARDS, { keepState: true });
      if (seq !== current.seq) { opened.release(); return; }
      const old = hold.panels;
      hold.panels = opened;
      old.release();
      current.text = text;
      showTimeBar(!hasPlayback(parseBase(text)));
    } finally { refreshing = false; }
  }
  feed.addEventListener('values', refresh);
  feed.addEventListener('node', refresh);
  keep.addEventListener('click', async () => {
    if (!current.path || !window.__dxBoards) return;
    const e = feed.dataset('entities').latestAt(Infinity, current.path);
    try {
      const id = await window.__dxBoards.create({ title: `Explore ${(e && e.name) || current.path}`, text: detail.layoutText ? detail.layoutText() : current.text });
      go('dashboards', { board: id });
    } catch (err) { crumbs.textContent = `Not kept: ${err.message}`; }
  });
  await select(params.sel);
  return {
    update(p) { if (p.sel !== (current.path || '')) return select(p.sel); return null; },
    unmount() { feed.removeEventListener('values', refresh); feed.removeEventListener('node', refresh); if (current.release) current.release(); current.seq += 1; if (sizer) sizer.destroy(); },
  };
}

// ---- a destination that is one .base file (Activity, Runs) ---------------------------------------------------------------------

function mountFile(file, title) {
  return async (root) => {
    root.innerHTML = `<div class="dx-board-view"><div class="dx-bar"><h1>${esc(title)}</h1><span class="dx-grow"></span>
      <a class="dx-link" href="${esc(new URL(file, BOARDS).pathname)}" target="_blank" rel="noopener" title="The .base file this page draws">${esc(file)}</a></div>
      <data-dashboard id="dx-${esc(file.replace(/\.base$/, ''))}"></data-dashboard></div>`;
    const el = root.querySelector('data-dashboard');
    const text = await template(file);
    const opened = await openBase(el, text, BOARDS);
    showTimeBar(!hasPlayback(opened.base));
    return { unmount() { opened.release(); } };
  };
}

// ---- Settings ---------------------------------------------------------------------------------------------------------------------

const THEME = 'robotdx.dx.theme';
function applyTheme(value) {
  if (value === 'light' || value === 'dark') document.documentElement.dataset.theme = value; else delete document.documentElement.dataset.theme;
}
applyTheme(store.get(THEME));

async function mountSettings(root) {
  root.innerHTML = `<div class="dx-board-view"><div class="dx-bar"><h1>Settings</h1></div><div class="dx-settings">
    <label>Sidebar <select data-setting="rail" aria-label="Sidebar style"></select></label>
    <p>Icons only, Figma's labels under the icons, or Shopify's wide list (it drags wider; double-click its edge to reset). Ctrl+Alt+R folds it and opens it again.</p>
    <label>Theme <select data-setting="theme" aria-label="Theme"><option value="system">As the system</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
    <dl id="dx-host"></dl>
  </div></div>`;
  const unbind = bindRailSelect(root.querySelector('select[data-setting="rail"]'), rail);
  const theme = root.querySelector('select[data-setting="theme"]');
  theme.value = store.get(THEME) || 'system';
  theme.addEventListener('change', () => { store.set(THEME, theme.value === 'system' ? null : theme.value); applyTheme(theme.value); });
  const dl = root.querySelector('#dx-host');
  let s = null, version = '';
  try { s = await (await fetch('/api/v1/settings', { cache: 'no-store' })).json(); } catch (_) { /* shown as unknown */ }
  try { version = await (await fetch(new URL('../vendor/data-ui/VERSION', import.meta.url), { cache: 'no-store' })).text(); } catch (_) { /* not vendored */ }
  const win = s && s.history_window_s ? `${Math.round(s.history_window_s / 60)} min (${esc(s.source || '')})` : 'unknown';
  dl.innerHTML = `<dt>History window</dt><dd>${win} · the host keeps levels and measurements this long, and so does this page's time bar. Change it on the <a class="dx-link" href="/">classic page</a> (View options).</dd>
    <dt>Host</dt><dd>${esc(feed.host.fixture ? `fixture ${feed.host.fixture.name} · ${feed.host.fixture.label}` : 'live')}</dd>
    <dt>data-ui</dt><dd><pre>${esc(version.split('\n').slice(0, 6).join('\n'))}</pre></dd>`;
  return { unmount() { if (unbind) unbind(); } };
}

// ---- the router ---------------------------------------------------------------------------------------------------------------------

const MOUNT = {
  explore: mountExplore,
  activity: mountFile('activity.base', 'Activity'),
  dashboards: (root, params) => mountDashboards(root, params, { openBase, go, showTimeBar, BOARDS, feed }),
  runs: mountFile('runs.base', 'Runs'),
  settings: mountSettings,
};
let shown = { dest: null, params: null, handle: null };
let rendering = Promise.resolve();

export function render() {
  rendering = rendering.then(async () => {
    const params = readUrl();
    rail.current = params.go;
    if (shown.dest === params.go && shown.handle && shown.handle.update) {
      const done = await shown.handle.update(params);
      if (done !== false) { shown.params = params; return; }
    }
    if (shown.handle && shown.handle.unmount) shown.handle.unmount();
    showTimeBar(true);
    main.innerHTML = '';
    main.dataset.dest = params.go;
    shown = { dest: params.go, params, handle: null };
    try { shown.handle = await MOUNT[params.go](main, params); } catch (err) {
      main.innerHTML = `<p class="dx-empty dx-error">This page could not draw ${esc(params.go)}: ${esc(err.message)}</p>`;
      console.error(err);
    }
  });
  return rendering;
}

rail.addEventListener('data-rail-navigate', (e) => {
  if (!DESTS.includes(e.detail.id)) return;   // Classic page is a real link
  e.preventDefault();
  go(e.detail.id);
});
addEventListener('popstate', () => render());

// a link to a moment pauses there; #t=live (or none) follows (PEP 0008 §4)
function readT() {
  const t = new URLSearchParams(location.hash.slice(1)).get('t');
  return t && t !== 'live' && Number.isFinite(Number(t)) ? Number(t) : null;
}

(async () => {
  await feed.start();
  startHeartbeat(feed);
  timeBar.base = { views: [{ type: 'playback', name: 'Time' }] };
  timeBar.data = feed.dataset('summary');
  await render();
  const t = readT();
  if (t !== null) clock.seek(t, { source: 'url' });
})();
