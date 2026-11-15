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
//
// Round 3 (docs/round3-brief-2026-10-23.md, seams in docs/round2-seams.md §6): one View options popover beside the
// search box (Group by = the structure, Sort by, the structure's toggles, tree style, skin, mode); resizable panes;
// the Table's inspector folds away; the detail panel is drawn by a DETAIL LAYOUT (window.IODetailLayouts,
// detail/*.js) and its utilities by a UTILITY MENU (window.IOUtilityMenus, utilities/*.js), both switched from the
// temporary bottom-right Prototype picker.
//
// Round 3.5 (docs/round2-seams.md §6-§7): the default skin is stock shadcn/ui, with a skin switcher back in the
// bottom-right bar; the utilities live in ONE place, a Utilities ▾ dropdown the shell draws in every entity page's
// header (a dry-run sheet under it, a quiet run line under the header, the result in Activity); every drill-down is a
// browser-history entry (back / forward, the mouse's buttons); one shared history cache and a page-level drill seam
// for the layouts; the Computer opens in d4 unless the picker says otherwise.
//
// Runs (docs/runs-v1.md, 2026-10-24): every real utility is a run on a terminal. The page starts it detached
// (POST /api/v1/act `detach`), keeps the run's id on the utility, reads its progress from /api/v1/state `runs`, and
// hands the run to the selected RUN VIEW (window.IORunViews, runs/*.js; r1 is the default): a ctx (makeRunsCtx) with the
// runs, their live output (xterm.js, vendor/xterm), typing, Stop and a place to draw. A third select in the bottom-right
// Prototype picker switches the run view.
'use strict';

const params = new URLSearchParams(location.search);
// threed: the 3D view (threed/core.js, docs/robot-description.md), the robot's parts coloured by their devices, the tree kept beside it
// deploy / teleop / data: the placeholder workspaces for the layers not built yet (layers/layers.js), the tree kept beside them
const VIEWS = ['tree', 'table', 'activity', 'threed', 'deploy', 'teleop', 'data'];
const S = {
  view: VIEWS.includes(params.get('view')) ? params.get('view') : 'tree',
  cam: params.get('cam') || null, // the 3D view's camera pose (threed/scene.js cameraText), part of its history entry
  sel: params.get('group') ? null : params.get('sel'),
  query: '',
  data: null,          // /api/v1/state
  act: null,           // /api/v1/activity
  actAt: 0,
  node: null,          // /api/v1/node for S.sel
  nodeAt: 0,
  open: new Map(),     // row key -> true/false, for the table and the built-in fallback tree
  selKey: params.get('group'), // the selected row when it stands for no entity (a structure's own grouping row)
  // the tree's two narrowings (view/core.js reroot / keepInside), both in the URL so back / forward and a reload keep them:
  into: params.get('into') || null,                              // stepped into this row (an entity path, else a row key)
  inside: (params.get('inside') || '').split(',').filter(Boolean), // only what is inside these rows (chips above the tree)
  // the page-level drill below the selection (ctx.diagnostic / ctx.page): one browser-history entry per step
  drill: { diag: params.get('diag'), dpath: params.get('dpath'), m: params.get('m'), page: params.get('page') },
  built: null,         // IOView.build(...) of the active structure for the current state
  structure: null,     // active structure id (view/structure/*.js)
  toggles: {},         // structure id -> {option key: bool}: the structure's own declared toggles
  skins: [],           // ready skins from skins/index.json
  skin: null,
  mode: 'auto',        // 'auto' (prefers-color-scheme) | 'light' | 'dark'
  util: null,          // the utility being dry-run / confirmed / run: {path, name, node, newId, phase, dry, result, ...}
  newIds: new Map(),   // entity path -> what the person typed in set_id's "New id (1-126)" box
  setup: null,         // the Expected setup edit being confirmed / saved: {path, op, phase, reason, rig, result}
  modalHtml: '',
  why: new Map(),      // entity path -> {checks: [{name, level, message, lines, evidence}], message, at}: what_to_check
  detailHtml: '',
  chip: params.get('chip') === '1',
  lastOk: 0,
  lastError: '',
  variant: null,       // the tree proposal drawing the Tree view (set by setupSwitcher)
  sort: 'status',      // View options' Sort by: status | name | change (view/core.js sortRows)
  connected: false,    // Connected only: the tree keeps what answers now (view/core.js keepConnected); the page beats /api/v1/presence
  presence: null,      // the host's answer to the latest beat: its presence process per adapter (docs/api-v1.md "Presence")
  pickable: [],        // the structure ids View options' Group by lists (manifest `group`, in `pick` order)
  vo: false,           // the View options popover is open
  inspOpen: false,     // the Table's inspector, opened by a row click (folds away again)
  inspPinned: false,   // ... or pinned open (the toolbar toggle, `]`)
  layout: null,        // the detail layout drawing the detail panel (detail/*.js)
  menu: null,          // the utility menu drawing the Utilities section (utilities/*.js)
  runView: null,       // the run view drawing runs (runs/*.js): r1 by default
  chartView: null,     // the chart view enlarging a sparkline (charts/*.js): c0 (nothing enlarges) by default
  closed: new Set(),   // detail section ids the viewer folded (remembered per section)
  opened: new Set(),   // detail section ids the viewer opened that start folded (ctx.section(..., {defaultOpen: false}))
  layoutChoice: null,  // the Prototype picker's explicit detail layout (remembered), or null: the per-entity default
  menuOpen: false,     // the entity header's Utilities ▾ dropdown
  runs: new Map(),     // entity path -> its utility run (running / done) while the viewer looks at another entity
  runDetails: false,   // the result line's Details ▸ is open
  sheetMore: false,    // the dry-run sheet's "How it runs ▸" (its steps, the plugin command line) is open
  settings: null,      // GET /api/v1/settings (the app-wide history window)
  cache: new Map(),    // url -> {data, error, loading, at, used}: the shared history cache (ctx.history, ctx.bar)
  byPath: null,        // path -> state node, of the last render
  scrolls: new Map(),  // a drill step (its URL) -> the detail's scroll position, restored on back / forward
  drawnAt: '',         // the drill step the detail last drew
};
const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
const $ = (id) => document.getElementById(id);
const esc = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
// A USB link is named one way on this page: web/usb.js's label ("USB 2.0", "USB 3.2 Gen 1"), never "480 Mb/s". Used for every
// visible name or message that can carry a link speed; the tooltip keeps the API's own words (Zach, 2026-10-24).
const usb = (text) => (window.IOUsb ? window.IOUsb.label(text) : String(text ?? ''));
const icon = (name, cls = 'ic') => `<svg class="${cls}"><use href="#i-${name || 'device'}"/></svg>`;
const lvl = (level) => level || 'none';
// a problem pill IS a diagnostic result: tagged so (the warn-colours rule), for every layout that draws one
const pill = (level) => `<span class="pill ${lvl(level)}"${PROBLEM.has(level) ? ' data-diagnostic' : ''}>${level || '—'}</span>`;
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
  sort: 'io-diagnostics.sort',
  treeWidth: 'io-diagnostics.pane.tree',
  inspectorWidth: 'io-diagnostics.pane.inspector',
  inspectorPinned: 'io-diagnostics.inspector-pinned',
  layout: 'io-diagnostics.detail-layout',
  menu: 'io-diagnostics.utility-menu',
  runView: 'io-diagnostics.runs-view',
  chartView: 'io-diagnostics.chart-view',
  sections: 'io-diagnostics.detail-sections',
  sectionsOpen: 'io-diagnostics.detail-sections-open',
  connected: 'io-diagnostics.connected-only',
};
function readPref(key) {
  try { return localStorage.getItem(key); } catch (_) { return null; }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value); } catch (_) { /* private mode: the choice lasts this page only */ }
}

// ---------------------------------------------------------------------------------- data ----
// WHY a bound on every load (Zach, 2026-11-04: "surely maybe there is like a timeout on these things?"): a fetch has none of its own, so
// a host that does not answer left a page on "loading…" for as long as the tab stayed open.
const LOAD_TIMEOUT_MS = 8000;
async function getJson(url, { timeoutMs = LOAD_TIMEOUT_MS, signal = null } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  if (signal) signal.addEventListener('abort', () => controller.abort(), { once: true });
  try {
    const response = await fetch(url, { signal: controller.signal });
    const data = await response.json();
    if (data.error) throw Object.assign(new Error(data.error), { status: response.status });
    return data;
  } catch (error) {
    if (controller.signal.aborted && !(signal && signal.aborted)) throw new Error(`no answer in ${Math.round(timeoutMs / 1000)} s`);
    throw error instanceof TypeError ? new Error('the host is not answering') : error; // WHY: fetch's own "Failed to fetch" says nothing to a person
  } finally {
    clearTimeout(timer);
  }
}

// WHY (Zach, 2026-11-04: "Preview server unreachable" on a host restarting, and sps restarts it on every .py that lands): the release chip is the
// runtime's own bundle and calls one failed poll "unreachable" until its next poll 5 s later. A restart is a second or two, so its status
// poll is asked again here for a few seconds first; a host that really is gone still fails, and the chip then says so. The chip gives a
// poll 8 s of its own (stable_preview_switcher REQUEST_TIMEOUT_MS), which this stays inside.
const CHIP_STATUS = '/__sps/status';
const CHIP_RIDE_MS = 6000;
const CHIP_RETRY_MS = 400;
(function rideOutRestarts() {
  const plain = window.fetch.bind(window);
  window.fetch = async function fetchThroughRestarts(input, init) {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    if (new URL(url, location.href).pathname !== CHIP_STATUS) return plain(input, init);
    const giveUp = performance.now() + CHIP_RIDE_MS;
    for (;;) {
      try {
        const response = await plain(input, init);
        if (response.status < 500 || performance.now() >= giveUp) return response;
      } catch (error) {
        if ((init && init.signal && init.signal.aborted) || performance.now() >= giveUp) throw error;
      }
      await new Promise((resolve) => setTimeout(resolve, CHIP_RETRY_MS));
    }
  };
})();

// --------------------------------------------------------------- skins, structures (manifests) ----
// skins/index.json and view/structure/index.json pre-list every planned entry; only `ready` ones are loaded, so a
// lane that lands a skin or a structure flips its own line and nothing else (docs/round2-seams.md).
async function readManifest(url, listKey) {
  try {
    const manifest = await getJson(url, { timeoutMs: 30000 }); // WHY longer: without a manifest there is no page to say anything on
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
// WHY shadcn: Zach, 2026-10-23: "I have outgrown the bare metal, it's honestly hurting my eyes now. Lets please just use
// stock shadcn for now ... what matters is the location of things"; every other skin stays one pick away (bottom-right)
// WHY d4 for the Computer (Zach, 2026-10-23, via the orchestrator): its page is the ROS2 System Webview's data (RAM, CPU,
// swap, load, disks, temperatures, GPU, network, USB); every other entity keeps d0 until he picks one
const DEFAULTS = { skin: 'shadcn', structure: 'p6', variant: 'v6', sort: 'status', layout: 'd6', computerLayout: 'd6', menu: 'u0', runs: 'r1', charts: 'c6' };
const darkQuery = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

// <html data-skin data-mode>: the skin's tokens for the mode. Auto follows the OS until the viewer picks one.
function applyTheme() {
  const html = document.documentElement;
  html.dataset.skin = S.skin || DEFAULTS.skin;
  html.dataset.mode = S.mode === 'light' || S.mode === 'dark' ? S.mode : (darkQuery && darkQuery.matches ? 'dark' : 'light');
  html.dataset.modePref = S.mode;
  // WHY: theme-dependent tree markup (Terminal guides) invalidates on preference changes, independent of data traffic.
  if (S.data) render();
}

// WHY: the existing change-only API avoids decoding/rebuilding a whole rack every second.
// Native EventSource owns reconnect/Last-Event-ID; polling is only a disconnected fallback.
const ClassicLive = window.IOClassicLive = { source: null, connected: false, rows: new Map(), root: null, dirty: false, clockAt: 0, clockBase: null, initialized: false, firstSnapshot: true, pending: [] };
function rememberTree(tree) {
  ClassicLive.rows.clear();
  ClassicLive.root = tree.path;
  (function walk(node) {
    ClassicLive.rows.set(node.path, { ...node, children: node.children.map((child) => child.path) });
    node.children.forEach(walk);
  })(tree);
}
function materializeTree() {
  if (!ClassicLive.dirty) return;
  const nest = (path) => {
    const row = ClassicLive.rows.get(path);
    return row ? { ...row, children: row.children.map(nest).filter(Boolean) } : null;
  };
  const tree = nest(ClassicLive.root);
  if (tree) S.data.tree = tree;
  ClassicLive.dirty = false;
}
function acceptedState() {
  S.lastOk = performance.now(); S.lastError = '';
  ClassicLive.clockAt = performance.now(); ClassicLive.clockBase = { ...S.data.clock };
  if (window.IORuns && S.data.runs) { window.IORuns.ingest(S.data.runs); syncRunUtilities(); }
  if (!S.sel && !S.selKey) {
    S.sel = (S.data.summary.attention[0] || {}).path || S.data.tree.path;
    replaceNav();
  }
  if (!history.state) replaceNav();
}
let activityLoading = false;
function loadActivity() {
  if (activityLoading || document.hidden) return;
  activityLoading = true;
  getJson('/api/v1/activity').then((act) => { S.act = act; S.actAt = performance.now(); if (S.sort === 'change') render(); else redrawDetail(); })
    .catch(() => {}).finally(() => { activityLoading = false; });
}
function applyLive(kind, data) {
  // WHY: /state bootstraps a page with no feed (existing HTTP adapters/proxies), but once there is one its snapshot is the base, whichever landed first: it is what the feed's deltas
  // follow from. The two are separate requests, so a /state read a moment before a change and a snapshot taken a moment after left that change in neither. The server is up before the
  // drivers have answered, so at a cold start a board could stay out of the tree until its row next changed, on a healthy board never (tests/test_instant_pages.js).
  if (kind === 'snapshot' && ClassicLive.firstSnapshot) {
    ClassicLive.firstSnapshot = false;
    if (!ClassicLive.initialized) { ClassicLive.initialSnapshot = data; return; }
  }
  if (!ClassicLive.initialized) {
    ClassicLive.pending.push([kind, data]);
    if (ClassicLive.pending.length > 2000) {
      if (ClassicLive.source) ClassicLive.source.close();
      ClassicLive.source = null; ClassicLive.connected = false; ClassicLive.pending = [];
    }
    return;
  }
  if (kind === 'samples') {
    const layout = layouts().d6;
    if (layout && layout.samples) layout.samples(data);
    return;
  }
  if (kind === 'snapshot') {
    S.data = data;
    rememberTree(data.tree);
    ClassicLive.dirty = false;
    const layout = layouts().d6;
    if (layout && layout.resetLive) layout.resetLive();
    acceptedState();
    loadActivity();
    S.nodeAt = 0;
  } else {
    if (!S.data) return;
    if (data.table === 'tree') {
      if (kind === 'remove') ClassicLive.rows.delete(data.id); else ClassicLive.rows.set(data.id, data.row);
      ClassicLive.dirty = true;
    } else if (data.table === 'state') {
      if (kind === 'remove') delete S.data[data.id]; else S.data[data.id] = data.row;
      if (data.id === 'clock') { ClassicLive.clockAt = performance.now(); ClassicLive.clockBase = { ...data.row }; }
      if (data.id === 'recent') loadActivity();
      if (data.id === 'runs' && window.IORuns) { window.IORuns.ingest(S.data.runs || []); syncRunUtilities(); }
    }
    S.lastOk = performance.now(); S.lastError = '';
  }
  render();
  if (S.sel) pollNode();
}
ClassicLive.apply = applyLive;
function startLiveFeed() {
  if (ClassicLive.source || document.hidden || typeof EventSource === 'undefined') return;
  const source = ClassicLive.source = new EventSource('/api/v1/live');
  source.onopen = () => { ClassicLive.connected = true; S.lastError = ''; };
  source.onerror = () => {
    ClassicLive.connected = false; ClassicLive.firstSnapshot = false;
    // WHY: on an HTTP error (a host still starting) the browser closes the stream for good; the next tick opens another
    if (source === ClassicLive.source && source.readyState === EventSource.CLOSED) ClassicLive.source = null;
  };
  for (const kind of ['snapshot', 'upsert', 'remove', 'samples']) source.addEventListener(kind, (event) => {
    if (source !== ClassicLive.source) return;
    try { applyLive(kind, JSON.parse(event.data)); } catch (error) { console.error('classic live:', error); }
  });
}
// WHY one at a time (the same pile-up as /node's): the 1 Hz tick asked again whether or not the last poll had answered, so a host that was not
// answering collected one more with every tick, each to be answered in turn when it came back.
let statePolling = false;
async function pollState() {
  if (statePolling) return;
  statePolling = true;
  try {
    const data = await getJson('/api/v1/state');
    // A late fallback answer cannot roll back an already connected feed.
    if (!ClassicLive.initialized || !ClassicLive.connected) {
      const base = ClassicLive.initialSnapshot || data;
      S.data = base; rememberTree(base.tree); acceptedState();
      ClassicLive.initialized = true;
      delete ClassicLive.initialSnapshot;
      const pending = ClassicLive.pending; ClassicLive.pending = [];
      for (const [kind, event] of pending) applyLive(kind, event);
      render();
    }
    if (!S.act || performance.now() - S.actAt > 4000) loadActivity();
  } catch (error) {
    S.lastError = String(error);
    if (!S.data) { $('tree-host').hidden = true; $('left-other').hidden = false; $('left-other').innerHTML = `<div class="err">${esc(error)}</div>`; }
  } finally {
    statePolling = false;
  }
}
async function tick() {
  if (document.hidden) return;
  startLiveFeed();
  if (!ClassicLive.initialized || !ClassicLive.connected) await pollState();
  if (S.data && S.sel && $('detail').checkVisibility()) pollNode();
  // WHY: the note above a stale page says how old it is, and nothing else redraws it while nothing arrives
  if (S.lastError || NodeLoad.failedPath) queueRedraw();
  renderStatusbar();
  renderClock();
}
document.addEventListener('visibilitychange', () => {
  // WHY: a background browser tab neither polls nor keeps hardware watches alive.
  if (document.hidden) {
    if (ClassicLive.source) ClassicLive.source.close();
    ClassicLive.source = null; ClassicLive.connected = false;
  } else { tick(); render(); }
});

function renderClock() {
  const base = ClassicLive.clockBase;
  if (!S.data || !base) return;
  // WHY: emitted uses wall time; the visible clock uses the host's replay clock and declared time zone.
  const now = base.now + (performance.now() - ClassicLive.clockAt) / 1000;
  const shown = new Date((now + (base.utc_offset_s || 0)) * 1000).toISOString();
  S.data.clock = { ...base, now, iso: shown.slice(0, 19) + String(base.iso || '').slice(19), time: shown.slice(11, 19) };
  $('clock').textContent = `${shown.slice(5, 10)}  ${shown.slice(11, 19)}`;
}

// A stock check that did not run (the API's `not_run: true`) says nothing about the entity (io_diagnostics/not_run.py): the host leaves it out
// of every level, so the page leaves it out of every "worst of its checks" too. `checks` keeps the ones that ran, whatever layout reads them;
// `notRun` holds the rest for the one quiet line that lists them (detail/d1x.js), grey and never OK.
function notRunSplit(detail) {
  if (!detail || !Array.isArray(detail.checks) || !detail.checks.some((check) => check.not_run)) return detail && { notRun: [], ...detail };
  return { ...detail, checks: detail.checks.filter((check) => !check.not_run), notRun: detail.checks.filter((check) => check.not_run) };
}

// One /node at a time, and its answer counts. WHY (measured 2026-11-04, tests/test_instant_pages.js): while a page had no answer every 1 Hz tick and
// every live event asked again, about 20 a second, and an answer counted only if it was the newest asked for. A /node slower than that gap could never
// land: with 3 s the page sent 645 requests in 30 s and drew nothing. Preview's took 4 to 22 s, so "loading…" was for ever. Chrome holds identical GETs back
// and sends them one at a time (a 4 s /node: 532 sent, 7 answered in 30 s, the newest queued 22 s), so the host always had the next one: its 90% CPU.
let pendingNode = null;
const NODE_TIMEOUT_MS = 6000, NODE_TIMEOUT_MAX_MS = 30000;
const NODE_RETRY_MS = 2000, NODE_RETRY_MAX_MS = 15000;
const NODE_GONE_MS = 20000; // how long an entity may be missing from the host and the tree before the page stops asking for it
const NODES_KEPT = 64;
const NodeLoad = { path: null, controller: null, again: false, failures: 0, failedPath: null, why: '', retryAt: 0, missingSince: null };
// WHY kept: a page already visited comes back at once, whole, and fills in behind (Zach, 2026-11-04: "Pages are just there"). Its age is shown
// when the host does not answer. A Map keeps insertion order: the oldest go first.
const nodeCache = new Map();
function rememberNode(path, node) {
  nodeCache.delete(path);
  // WHY the tree's row too: the row of an entity the tree has just lost (a restarted host, before its driver has reported) keeps the page drawn until it comes back or stays gone
  nodeCache.set(path, { node, at: performance.now(), row: (S.byPath && S.byPath.get(path)) || null });
  for (const key of [...nodeCache.keys()].slice(0, Math.max(0, nodeCache.size - NODES_KEPT))) nodeCache.delete(key);
}
// The poll: its own answer is on the way, so asking again could only be slower
const pollNode = () => {
  if (NodeLoad.path !== S.sel && (performance.now() - S.nodeAt > 1800 || !S.node || S.node.node.path !== S.sel)) loadNode();
};
// Something changed (a run ended, a setting was written, the selection moved): the answer must be newer than this moment. WHY one more after the
// one in flight and not a second beside it: that answer may predate the change, and one at a time is what keeps the host and the browser's connections free.
function loadNode(again = false) {
  const path = S.sel;
  if (!path) return;
  if (NodeLoad.path === path) { NodeLoad.again = true; return; }
  const failures = NodeLoad.failedPath === path ? NodeLoad.failures : 0;
  if (!again && failures && performance.now() < NodeLoad.retryAt) return;
  if (NodeLoad.controller) NodeLoad.controller.abort(); // another entity: that answer is no longer wanted
  const controller = new AbortController();
  NodeLoad.path = path; NodeLoad.controller = controller; NodeLoad.again = false;
  S.nodeAt = performance.now();
  // WHY longer each time: a host that needs 9 s still lands its answer; a fixed bound would abort it for ever, the same livelock
  const timeoutMs = Math.min(NODE_TIMEOUT_MS * 2 ** failures, NODE_TIMEOUT_MAX_MS);
  getJson(`/api/v1/node?path=${encodeURIComponent(path)}`, { timeoutMs, signal: controller.signal })
    .then((node) => {
      if (NodeLoad.controller !== controller) return;
      const more = NodeLoad.again;
      Object.assign(NodeLoad, { path: null, controller: null, again: false, failures: 0, failedPath: null, why: '', retryAt: 0, missingSince: null });
      rememberNode(path, node);
      if (S.sel === path) { pendingNode = { path, node }; queueRedraw(); if (more) loadNode(); }
    })
    .catch((error) => {
      if (NodeLoad.controller !== controller) return; // superseded by a new selection
      NodeLoad.path = NodeLoad.controller = null; NodeLoad.again = false;
      // WHY only an entity missing for a while moves the selection (Zach, 2026-11-04: "no lost selection"): a host restarting, or a slow one, says nothing about it, and
      // a board's row comes back only when its driver has reported, seconds after the host is up. One that stays missing was unplugged: the Computer, as ever.
      const missing = error.status === 404 && S.sel === path && S.data && !(S.byPath && S.byPath.has(path));
      if (missing) {
        NodeLoad.missingSince = NodeLoad.missingSince === null ? performance.now() : NodeLoad.missingSince;
        if (performance.now() - NodeLoad.missingSince >= NODE_GONE_MS) {
          Object.assign(NodeLoad, { failures: 0, failedPath: null, why: '', retryAt: 0, missingSince: null });
          S.sel = S.data.tree.path; S.node = null; S.drill = { ...NO_DRILL }; replaceNav();
          return;
        }
      } else NodeLoad.missingSince = null;
      Object.assign(NodeLoad, { failures: failures + 1, failedPath: path, why: String(error.message || error),
        retryAt: performance.now() + Math.min(NODE_RETRY_MS * 2 ** failures, NODE_RETRY_MAX_MS) });
      queueRedraw();
    });
}
// The calm line above a page that is older than it looks: how old, why, and Retry. Never a problem colour: nothing is known to be wrong with
// the entity, only that the host has not said yet.
function loadNote(node) {
  const reconnecting = Boolean(S.lastError);
  const failed = NodeLoad.failedPath === S.sel; // WHY S.sel: an entity not in the tree yet draws the Computer's row, and its note still belongs on the page
  if (!reconnecting && !failed) return '';
  const held = nodeCache.get(S.sel);
  const shown = held ? `Showing what the host said ${ago((performance.now() - held.at) / 1000)} ago.` : 'Showing what the tree last knew.';
  const text = reconnecting ? `Reconnecting to the host. ${shown}` : `The details of this page have not answered (${esc(NodeLoad.why)}). ${shown}`;
  return `<div class="banner osonly" role="status" data-load-slow><span>${text}</span>${reconnecting ? '' : '<button class="load-retry" data-load-retry>Retry</button>'}</div>`;
}

// ----------------------------------------------------------------------------------- back / forward ----
// Every drill-down is a page of the browser's history (Zach, 2026-10-23: "As I drill down please support
// forwards/backwards buttons on the mouse"): an entity (or grouping row) selection, a diagnostic or measurement page
// below it, a layout's own sub-page and the Tree / Table / Activity switch each push a URL that restores them
// (?sel=<path>&diag=<status name>&view=tree), and popstate puts that exact state back. The mouse's back / forward
// buttons and Alt+← / → are then the browser's own, in a tab and in the Chrome --app window.
// WHY in the shell, once: d1-d5 each kept a private drill stack the browser could not see; they read ctx.diagnostic.
// `cam` (2026-10-29): the 3D view's camera, only in a 3D entry, so Open from 3D and then Back returns to the same view of the robot
const NAV_KEYS = ['sel', 'group', 'diag', 'dpath', 'm', 'page', 'view', 'into', 'inside', 'cam'];
const NO_DRILL = { diag: null, dpath: null, m: null, page: null };
const navNow = () => ({ sel: S.selKey ? null : S.sel || null, group: S.selKey || null, ...S.drill, view: S.view, into: S.into || null, inside: S.inside.join(',') || null,
  cam: S.view === 'threed' ? S.cam || null : null });
function navUrl(nav) {
  const query = new URLSearchParams();
  for (const key of NAV_KEYS) if (nav[key]) query.set(key, nav[key]);
  return `${location.pathname}?${query}`;
}
const sameNav = (a, b) => Boolean(a && b) && NAV_KEYS.every((key) => (a[key] || null) === (b[key] || null));
// one level up: a measurement's diagnostic, a diagnostic's (or a sub-page's) entity; an entity has none
function parentNav(nav) {
  if (nav.m) return { ...nav, m: null };
  if (nav.diag || nav.page) return { ...nav, ...NO_DRILL };
  return null;
}
// `up`: the entry before this one is its parent, so the layout's own Back can be the browser's Back
function pushNav() {
  const nav = navNow();
  if (sameNav(history.state, nav)) return;
  history.pushState({ ...nav, up: sameNav(parentNav(nav), history.state) }, '', navUrl(nav));
}
function replaceNav() {
  const nav = navNow();
  history.replaceState({ ...nav, up: Boolean(history.state && history.state.up) }, '', navUrl(nav));
}
function applyNav(nav) {
  S.view = VIEWS.includes(nav.view) ? nav.view : 'tree';
  if (S.view === 'threed') S.cam = nav.cam || S.cam;
  S.into = nav.into || null;
  S.inside = String(nav.inside || '').split(',').filter(Boolean);
  if (nav.group) {
    if (S.selKey !== nav.group) { stashRun(null); S.selKey = nav.group; S.sel = null; S.node = null; S.menuOpen = false; }
  } else if (nav.sel && (nav.sel !== S.sel || S.selKey)) select(nav.sel);
  S.drill = { diag: nav.diag || null, dpath: nav.dpath || null, m: nav.m || null, page: nav.page || null };
  // a drill step restored in the Table is read in its inspector
  if (S.view === 'table' && (S.drill.diag || S.drill.page)) S.inspOpen = true;
}
window.addEventListener('popstate', (event) => {
  const nav = event.state || Object.fromEntries(NAV_KEYS.map((key) => [key, new URLSearchParams(location.search).get(key)]));
  applyNav(nav);
  S.menuOpen = false;
  render();
});

// The drill seam (ctx.openDiagnostic / ctx.openPage / ctx.closeDiagnostic): a diagnostic of the selection (or, with
// opts.path, of one of its parts), optionally one of its measurements; opts.select also selects opts.path first (a
// part's diagnostic opened as that part's own page).
function openDiagnostic(name, opts = {}) {
  if (!name) return;
  if (opts.select && opts.path && (opts.path !== S.sel || S.selKey)) select(opts.path);
  const dpath = opts.path && opts.path !== S.sel ? opts.path : null;
  S.drill = { diag: name, dpath, m: opts.measurement || null, page: null };
  S.menuOpen = false;
  pushNav();
  render();
}
function openPage(id) {
  if (!id) return;
  S.drill = { ...NO_DRILL, page: id };
  pushNav();
  render();
}
// One level up. When the entry before is that level (the viewer came down from it), this IS the browser's Back, so
// back / forward stay one linear list; otherwise the level above is pushed.
function closeDiagnostic() {
  if (history.state && history.state.up && sameNav(history.state, navNow())) { history.back(); return; }
  const up = parentNav(navNow());
  if (!up) return;
  S.drill = { diag: up.diag || null, dpath: up.dpath || null, m: up.m || null, page: up.page || null };
  pushNav();
  render();
}
const diagnosticOf = () => (S.drill.diag ? { name: S.drill.diag, path: S.drill.dpath || S.sel, measurement: S.drill.m || null } : null);

// --------------------------------------------------------------------------- the shared history cache ----
// One cache for every layout (the round-3 lanes' ask): /api/v1/history and /api/v1/history/bar, each URL fetched at
// most every HISTORY_EVERY_MS while a layout asks for it (the API asks 2-5 s); an answer that lands redraws the
// detail; an entry nobody asked for in a minute is dropped. A failed refresh keeps the last answer with its error.
const HISTORY_EVERY_MS = 3000;
function cached(url, everyMs = HISTORY_EVERY_MS) {
  const now = Date.now();
  let entry = S.cache.get(url);
  if (!entry) { entry = { data: null, error: '', loading: false, at: 0, used: now }; S.cache.set(url, entry); }
  entry.used = now;
  if (!entry.loading && now - entry.at >= everyMs) {
    entry.loading = true;
    fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(LOAD_TIMEOUT_MS) })
      .then(async (response) => {
        const body = await response.json().catch(() => ({}));
        if (!response.ok || body.error) throw new Error(body.error || `HTTP ${response.status}`);
        return body;
      })
      .then((data) => { entry.data = data; entry.error = ''; }, (error) => { entry.error = String(error.message || error); })
      .finally(() => { entry.loading = false; entry.at = Date.now(); queueRedraw(); });
  }
  if (S.cache.size > 96) for (const [key, old] of S.cache) if (!old.loading && now - old.used > 60000) S.cache.delete(key);
  return entry;
}
const historyOf = (path, opts = {}) => cached(`/api/v1/history?path=${encodeURIComponent(path)}${opts.status ? `&status=${encodeURIComponent(opts.status)}` : ''}`, opts.everyMs);
const barOf = (path, bins = 60, opts = {}) => cached(`/api/v1/history/bar?path=${encodeURIComponent(path)}&bins=${bins}`, opts.everyMs);

// WHY: network completions and interaction invalidations share one frame per view.
// A full-shell update subsumes the pending detail update, never paints it twice.
let renderQueued = false, fullRenderPending = false, detailRenderPending = false;
function flushRender() {
  if (document.hidden) return;
  if (pendingNode) {
    // WHY: publishing a loaded selection and its DOM in the same frame keeps navigation atomic.
    if (pendingNode.path === S.sel) S.node = notRunSplit(pendingNode.node);
    pendingNode = null;
  }
  const full = fullRenderPending, detail = detailRenderPending;
  fullRenderPending = detailRenderPending = false;
  if (full) renderNow(); else if (detail && S.data && S.byPath) renderDetail(S.byPath);
}
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => { renderQueued = false; flushRender(); });
}
const interacting = () => window.event && /^(click|dblclick|input|change|keydown|drop|pointer)/.test(window.event.type);
function redrawDetail() {
  detailRenderPending = true;
  // WHY: direct controls keep their existing immediate response; incoming data/fetches coalesce in rAF.
  if (interacting()) flushRender(); else scheduleRender();
}
function queueRedraw() { detailRenderPending = true; scheduleRender(); }
function render() { fullRenderPending = true; if (interacting()) flushRender(); else scheduleRender(); }

// The app-wide history window (GET / POST /api/v1/settings; View options). POST is page-only, as /api/v1/act.
const WINDOW_CHOICES = [60, 300, 600, 900, 1800, 3600, 10800, 21600, 43200, 86400];
const windowWords = (s) => (s < 3600 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);
async function loadSettings() {
  try { S.settings = await getJson('/api/v1/settings'); } catch (error) { S.settings = null; }
  renderWindowPicker();
  renderDepthControls();
}
function renderWindowPicker() {
  const picker = $('history-window');
  const settings = S.settings;
  $('window-switch').hidden = !settings;
  if (!settings) return;
  const limits = (settings.limits || {}).history_window_s || { min: 60, max: 86400 };
  const now = Number(settings.history_window_s);
  const choices = [...new Set([...WINDOW_CHOICES.filter((s) => s >= limits.min && s <= limits.max), now])].sort((a, b) => a - b);
  const html = choices.map((s) => `<option value="${s}">${esc(windowWords(s))}</option>`).join('');
  if (picker.innerHTML !== html) picker.innerHTML = html;
  picker.value = String(now);
  picker.title = `History window: every health bar and trend (${esc(settings.source || '')}${settings.note ? ` · ${settings.note}` : ''})`;
}
// The depth colour map (Zach, 2026-10-24: "in the settings of this app please give the option to set the color map for
// the depth map"): realsense-viewer's colour scheme names, its histogram equalization switch and, with that off, the
// distance range the colours span. App-wide like the history window; the host restarts a streaming camera's watch.
function renderDepthControls() {
  const settings = S.settings;
  const limits = (settings && settings.limits && settings.limits.depth_colormap) || null;
  const shown = Boolean(settings && limits && 'depth_colormap' in settings);
  $('depthmap-switch').hidden = $('depth-equalize-switch').hidden = !shown;
  $('depth-range-switch').hidden = !shown || settings.depth_equalize !== false;
  if (!shown) return;
  const picker = $('depth-colormap');
  const html = limits.choices.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`).join('');
  if (picker.innerHTML !== html) picker.innerHTML = html;
  picker.value = settings.depth_colormap;
  $('depth-equalize').checked = settings.depth_equalize !== false;
  // WHY not while typing: a render must not rewrite the number a person is in the middle of entering
  if (document.activeElement !== $('depth-min')) $('depth-min').value = String(settings.depth_min_m);
  if (document.activeElement !== $('depth-max')) $('depth-max').value = String(settings.depth_max_m);
}
async function setDepth(changes) {
  let answer;
  try {
    const response = await fetch('/api/v1/settings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' },
      body: JSON.stringify(changes) });
    answer = await response.json();
    if (!response.ok) throw new Error(answer.error || answer.refused || `HTTP ${response.status}`);
  } catch (error) {
    console.warn('depth colour map:', error);
    renderDepthControls(); // back to what the host holds
    return;
  }
  S.settings = answer;
  renderDepthControls();
}
async function setWindow(seconds) {
  let answer;
  try {
    const response = await fetch('/api/v1/settings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' },
      body: JSON.stringify({ history_window_s: seconds }) });
    answer = await response.json();
    if (!response.ok) throw new Error(answer.error || answer.refused || `HTTP ${response.status}`);
  } catch (error) {
    console.warn('history window:', error);
    renderWindowPicker(); // back to what the host holds
    return;
  }
  S.settings = answer;
  S.cache.clear(); // every bar and trend now answers over the new window
  renderWindowPicker();
  render();
}

// The chart views' hooks (charts/core.js, mount): the shared history cache, the app-wide window and settings, the entity names
// and a way to go to an entity. WHY a ctx and not globals: the same seam the run views have (makeRunsCtx); a chart view never
// reads S.
function makeChartsCtx() {
  return {
    fetch: (url, everyMs) => cached(url, everyMs),
    settings: () => S.settings,
    setWindow,
    // POST /api/v1/settings with `body` (the page-only write); resolves to the new settings or throws the host's reason
    async saveSettings(body) {
      const response = await fetch('/api/v1/settings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify(body) });
      const answer = await response.json();
      if (!response.ok) throw new Error(answer.error || answer.refused || `HTTP ${response.status}`);
      S.settings = answer;
      return answer;
    },
    nameOf: (path) => entityLabel(path),
    openEntity(path) { select(path); pushNav(); render(); },
    // the page-level drill (one browser-history entry per step): a chart view's own sub-page, id in the URL's `page`
    page: () => S.drill.page || null,
    openPage,
    closePage: closeDiagnostic,
    selected: () => S.sel,
    redraw: redrawDetail,
  };
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

// The tree's row menu (web/tree/v6.js): a utility of one row. Select the row, then start that utility's dry run as soon as
// its node (and so its actions) has loaded, exactly as a click in the Utilities menu would.
function onRowAction(name, row) {
  if (!row || !row.entity) return;
  onSelectEntity(row.entity);
  const began = performance.now();
  const go = () => {
    if (S.node && S.node.node && S.node.node.path === row.entity) { startDryRun(name); return; }
    if (performance.now() - began < 5000) setTimeout(go, 100);
  };
  go();
}

// ------------------------------------------------------------------------- step into, only inside ----
// Zach, 2026-10-24: "you can like filter to only show things inside certain things" (a chip above the tree) and step into a folder
// "like the VS Code extension enter-folder" (xiangda.enter-folder): a row's menu re-roots the tree at that row, a ← button (and
// Ctrl+Shift+Backspace) steps back out one level, the breadcrumb jumps to any level, and the tree's top row is the entered one.
// Both narrowings are applied by view/core.js to the rows of whatever structure is active; both live in the URL.
function treeNav() {
  const nav = document.createElement('div');
  nav.className = 'tree-nav';
  nav.hidden = true;
  nav.addEventListener('click', (event) => {
    const into = event.target.closest('[data-tn-into]');
    if (into) { stepInto(into.getAttribute('data-tn-into') || null); return; }
    if (event.target.closest('[data-tn-up]')) { stepUp(); return; }
    const chip = event.target.closest('[data-tn-unchip]');
    if (chip) removeInside(chip.getAttribute('data-tn-unchip'));
  });
  return nav;
}
const refOf = (crumb) => crumb.entity || crumb.key;
// a row can be entered when it has rows below it (a leaf is no folder); a reference the current rows do not have is let through
function canEnter(ref) {
  const found = ref && S.built ? IOView.findRow(S.built.rows, ref) : null;
  return !found || found.row.children.length > 0;
}
function stepInto(ref) {
  const next = ref || null;
  if (next === S.into || (next && !canEnter(next))) return;
  S.into = next;
  pushNav();
  render();
}
function stepUp() {
  const trail = (S.built && S.built.trail) || [];
  if (!trail.length) return;
  stepInto(trail.length > 1 ? refOf(trail[trail.length - 2]) : null);
}
function addInside(ref) {
  if (!ref || S.inside.includes(ref)) return;
  S.inside = [...S.inside, ref];
  pushNav();
  render();
}
function removeInside(ref) {
  S.inside = S.inside.filter((item) => item !== ref);
  pushNav();
  render();
}
// a row menu's choice (view/tree/v6.js onRowNav)
function onRowNav(kind, row) {
  const ref = row.entity || row.key;
  if (kind === 'into') stepInto(ref);
  else if (kind === 'inside') addInside(ref);
}
const TN_ARROW = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3 5 8l5 5"/></svg>';
const TN_HOME = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 7.5 8 3l5.5 4.5M4 6.7V13h3V9.5h2V13h3V6.7"/></svg>';
const TN_X = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 4 8 8M12 4l-8 8"/></svg>';
function renderTreeNav() {
  const bar = document.querySelector('#tree-host > .tree-nav');
  if (!bar) return;
  const built = S.built;
  const trail = (built && built.trail) || [];
  const chips = (built && built.inside) || [];
  const usable = Boolean(built) && !isLegacy(S.variant) && (trail.length || chips.length);
  let html = '';
  if (usable) {
    if (trail.length) {
      const last = trail.length - 1;
      html += '<div class="tn-trail" role="navigation" aria-label="Where the tree starts">'
        + `<button type="button" class="tn-btn" data-tn-up title="Back out one level ( Ctrl+Shift+Backspace )" aria-label="Back to the parent">${TN_ARROW}</button>`
        + `<button type="button" class="tn-btn" data-tn-into="" title="Back to the whole tree" aria-label="Back to the whole tree">${TN_HOME}</button>`
        + trail.map((crumb, at) => (at === last
          ? `<span class="tn-here" aria-current="location" title="${esc(crumb.label)}">${esc(crumb.label)}</span>`
          : `<button type="button" class="tn-crumb" data-tn-into="${esc(refOf(crumb))}" title="Start the tree at ${esc(crumb.label)}">${esc(crumb.label)}</button><span class="tn-sep">›</span>`)).join('')
        + '</div>';
    }
    if (chips.length) {
      html += `<div class="tn-chips" role="group" aria-label="Only what is inside">${chips.map((chip) => `<span class="tn-chip${chip.missing ? ' tn-missing' : ''}" title="${esc(chip.missing ? 'Not in this grouping' : `Only what is inside ${chip.label}`)}">`
        + `<span class="tn-chip-k">Inside</span><span class="tn-chip-v">${esc(chip.label)}</span>`
        + `<button type="button" class="tn-chip-x" data-tn-unchip="${esc(chip.ref)}" aria-label="Remove the filter: inside ${esc(chip.label)}" title="Remove this filter">${TN_X}</button></span>`).join('')}</div>`;
    }
  }
  if (bar.dataset.html !== html) { bar.dataset.html = html; bar.innerHTML = html; }
  bar.hidden = !html;
}

// a rows renderer reports a row key; legacy renderers report an entity path
function onTreeSelect(key) {
  const row = S.built && S.built.byKey.get(key);
  if (row && !row.entity) { // a structure's own grouping row: nothing to load from /api/v1/node
    if (S.selKey === key) return;
    stashRun(null);
    S.selKey = key; S.sel = null; S.node = null; S.drill = { ...NO_DRILL }; S.menuOpen = false;
    pushNav();
    render();
    return;
  }
  const entity = row ? row.entity : key;
  if (!entity || (entity === S.sel && !S.selKey)) return;
  select(entity);
  pushNav();
  render();
}
function onSelectEntity(entity) {
  if (!entity || (entity === S.sel && !S.selKey)) return;
  select(entity);
  pushNav();
  render();
}
// the row key a rows renderer should mark selected
const selectedKey = () => S.selKey || (S.built && S.sel ? S.built.byEntity.get(S.sel) || null : null);

function renderTreePane() {
  const id = S.variant;
  const renderer = id && renderers()[id];
  const host = $('tree-host');
  if (!renderer) { host.innerHTML = renderTree(); return; } // no proposal loaded: the page's own plain list
  for (const element of host.children) if (element.dataset.variant !== undefined) element.hidden = element.dataset.variant !== id;
  const pane = paneFor(id);
  pane.hidden = false;
  try {
    if (isLegacy(id)) renderer.render(pane, S.data.tree, { onSelect: onTreeSelect, selectedPath: S.sel, query: NO_QUERY.has(id) ? '' : S.query });
    else {
      renderer.render(pane, S.built.rows, { onSelect: onTreeSelect, onSelectEntity, onRowAction, onRowNav, selectedPath: selectedKey(), query: S.query,
        summary: S.data.summary, host: S.built.host, structure: S.structure });
    }
  } catch (error) {
    console.error(`tree ${id}:`, error);
    pane.innerHTML = `<div class="err">tree ${esc(id)} failed: ${esc(error)}</div>`;
  }
}

// -------------------------------------------------------------------------------- View options ----
// One sliders button beside the search box opens one popover (Zach, 2026-10-23: like Claude Transcripts' filter panel):
// Group by (the structure), Sort by, the structure's own toggles, then tree style, skin and Auto / Light / Dark. Every
// choice switches live and persists (never a URL flag). The bottom-right keeps only the temporary Prototype picker.
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

// Group by lists the structures the manifest names (`group`, in `pick` order); one marked picker=false (p3) keeps its
// file loaded for the seams but is no choice, and a remembered choice that is no longer listed falls back to the default.
function setupStructures(manifest) {
  const registered = IOView.structureIds();
  const entries = new Map(manifest.entries.map((entry) => [entry.id, entry]));
  const listed = (id) => !entries.has(id) || entries.get(id).picker !== false;
  const pick = (id) => (entries.has(id) && entries.get(id).pick !== undefined ? entries.get(id).pick : 50);
  const ids = registered.filter(listed).sort((a, b) => pick(a) - pick(b));
  S.pickable = ids;
  const stored = readPref(PREFS.structure);
  const fallback = ids.includes(manifest.default) ? manifest.default : ids.includes(DEFAULTS.structure) ? DEFAULTS.structure : ids[0] || null;
  S.structure = ids.includes(stored) ? stored : fallback;
  try { S.toggles = JSON.parse(readPref(PREFS.toggles) || '{}') || {}; } catch (_) { S.toggles = {}; }
  const picker = $('structure');
  const groupName = (id) => (entries.get(id) && entries.get(id).group) || window.IOStructures[id].name || id;
  picker.innerHTML = ids.map((id) => `<option value="${esc(id)}" title="${esc(`${id} · ${window.IOStructures[id].name || id} — ${window.IOStructures[id].description || ''}`)}">`
    + `${esc(groupName(id))}</option>`).join('');
  picker.value = S.structure || '';
  picker.addEventListener('change', () => {
    if (!window.IOStructures[picker.value]) return;
    S.structure = picker.value;
    writePref(PREFS.structure, S.structure);
    S.open.clear();
    renderToggles();
    render();
  });
  const sort = $('sort');
  const storedSort = readPref(PREFS.sort);
  S.sort = IOView.SORTS.includes(storedSort) ? storedSort : DEFAULTS.sort;
  sort.value = S.sort;
  sort.addEventListener('change', () => { if (!IOView.SORTS.includes(sort.value)) return; S.sort = sort.value; writePref(PREFS.sort, S.sort); render(); });
  renderToggles();
}

// The newest activity event per entity path (Sort by "Last change"): view/core.js reads it as options.changes.
function changesOf(act) {
  const changes = {};
  for (const event of (act && act.events) || []) {
    if (event.path && typeof event.stamp === 'number' && !(changes[event.path] >= event.stamp)) changes[event.path] = event.stamp;
  }
  return changes;
}

function setViewOptions(open) {
  S.vo = open;
  $('view-options').hidden = !open;
  $('vo-btn').setAttribute('aria-expanded', String(open));
  $('vo-btn').classList.toggle('on', open);
}
// the button's dot: something in the popover differs from the defaults (Claude Transcripts marks its filter the same way)
function renderViewOptionsDot() {
  const structure = window.IOStructures[S.structure] || {};
  const chosen = S.toggles[S.structure] || {};
  const toggled = (structure.toggles || []).some((toggle) => toggle.key in chosen && Boolean(chosen[toggle.key]) !== Boolean(toggle.default));
  $('vo-btn').classList.toggle('changed', S.structure !== (S.pickable.includes(DEFAULTS.structure) ? DEFAULTS.structure : S.pickable[0]) || S.sort !== DEFAULTS.sort || toggled);
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
  $('structure-toggles').innerHTML = (structure.toggles || []).filter((toggle) => IOView.toggleShown(toggle, options)).map((toggle) => `<label class="vo-row vo-switch" title="${esc(toggle.title || toggle.label)}">`
    + `<span class="vo-label">${esc(toggle.label)}</span><input type="checkbox" role="switch" data-opt="${esc(toggle.key)}"${options[toggle.key] ? ' checked' : ''}></label>`).join('')
    // the plug button's own setting, in every grouping (Zach, 2026-11-10: "give me the option to hide the expected ones")
    + `<label class="vo-row vo-switch" title="Hide what the model remembers but nothing hears now: stale boards, a rig's absent parts. The same as the plug button.">`
    + `<span class="vo-label">Only what's connected</span><input type="checkbox" role="switch" data-connected${S.connected ? ' checked' : ''}></label>`;
}
// ---- Connected only (Zach, 2026-11-09: "just show connected things"; docs/api-v1.md "Presence") ----
// While it is on and the page is in view, the page beats POST /api/v1/presence every few seconds: the host then keeps one query-only
// presence process per adapter, so a board plugged in or pulled shows within seconds instead of at the next full read (300 s).
// WHY only while it is on and visible: each pass holds the rack bus lock for a moment, and an experiment refuses to start while it is
// held; the host stops the processes about 10 s after the last beat.
const PRESENCE_BEAT_MS = 3000;
async function presenceBeat() {
  if (!S.connected || document.visibilityState !== 'visible') return;
  try {
    const response = await fetch('/api/v1/presence', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' },
      body: '{}', signal: AbortSignal.timeout(LOAD_TIMEOUT_MS) });
    if (!response.ok) return;
    S.presence = (await response.json()).presence || null;
    if (window.IOLayoutHost) window.IOLayoutHost.update();
  } catch (_) { /* the next beat asks again; the tree keeps showing what /state says */ }
}
function setConnected(on) {
  S.connected = Boolean(on);
  writePref(PREFS.connected, S.connected ? '1' : '0');
  if (S.connected) presenceBeat();
  renderToggles();
  render();
}
setInterval(presenceBeat, PRESENCE_BEAT_MS);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') presenceBeat(); });

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
  // the primary skins first (the default first: shadcn/ui, then Pretty), the Bare Metal family together, every other
  // ready skin under "More skins"; the same list in View options and in the bottom-right bar (Zach, 2026-10-23: "please
  // still give me the option to change the skin at the bottom"), kept in step
  const primary = S.skins.filter((skin) => skin.primary).sort((a, b) => (b.id === manifestDefault) - (a.id === manifestDefault));
  const family = S.skins.filter((skin) => !skin.primary && skin.family === 'bare-metal');
  const more = S.skins.filter((skin) => !skin.primary && skin.family !== 'bare-metal');
  const option = (skin) => `<option value="${esc(skin.id)}">${esc(skin.name || skin.id)}</option>`;
  const html = primary.map(option).join('') + (family.length ? `<optgroup label="Bare Metal">${family.map(option).join('')}</optgroup>` : '')
    + (more.length ? `<optgroup label="More skins">${more.map(option).join('')}</optgroup>` : '');
  for (const picker of [$('skin'), $('skin-bar')]) {
    picker.innerHTML = html;
    picker.value = S.skin;
    picker.addEventListener('change', () => {
      S.skin = picker.value;
      writePref(PREFS.skin, S.skin);
      $('skin').value = S.skin;
      $('skin-bar').value = S.skin;
      applyTheme();
    });
  }
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
  pushNav();
  render();
  $('left').scrollTop = scrollTops.get(id) || 0;
}

// ------------------------------------------------------------------------------------- panes ----
// Every pane boundary drags (Zach, 2026-10-23; Settings/Memory/feedback_resizable_panels.md): the Tree view's
// tree|detail sash sets the tree's width, the Table view's table|inspector sash the inspector's. Each width is
// remembered in this browser, a double-click restores the default, and every pane keeps a minimum width. The arrow
// keys move a focused sash by 16 px. WHY hand-rolled: the page is dependency-free (no bundler, no npm runtime).
const PANES = {
  tree: { pref: PREFS.treeWidth, variable: '--tree-w', fallback: 470, min: 280, other: 360 },        // other = the detail's minimum
  table: { pref: PREFS.inspectorWidth, variable: '--insp-w', fallback: 460, min: 320, other: 420 },  // other = the table's minimum
};
const paneOf = () => (S.view === 'table' ? PANES.table : PANES.tree);
function clampPane(pane, width) {
  const total = $('main').getBoundingClientRect().width || window.innerWidth;
  return Math.round(Math.max(pane.min, Math.min(width, total - pane.other - 6)));
}
function applyPanes() {
  for (const pane of Object.values(PANES)) {
    const stored = Number(readPref(pane.pref));
    $('main').style.setProperty(pane.variable, `${clampPane(pane, stored > 0 ? stored : pane.fallback)}px`);
  }
}
function setPane(pane, width, persist) {
  const value = clampPane(pane, width);
  $('main').style.setProperty(pane.variable, `${value}px`);
  if (persist) writePref(pane.pref, String(value));
  return value;
}
function setupPanes() {
  const sash = $('sash');
  let drag = null;
  sash.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    const pane = paneOf();
    const rect = $('main').getBoundingClientRect();
    drag = { pane, rect, id: event.pointerId };
    sash.setPointerCapture(event.pointerId);
    document.body.classList.add('resizing');
    event.preventDefault();
  });
  sash.addEventListener('pointermove', (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    // the tree grows to the right of the main area's left edge; the inspector grows to the left of its right edge
    drag.width = setPane(drag.pane, drag.pane === PANES.tree ? event.clientX - drag.rect.left : drag.rect.right - event.clientX, false);
  });
  const end = (event) => {
    if (!drag || event.pointerId !== drag.id) return;
    if (drag.width !== undefined) setPane(drag.pane, drag.width, true);
    drag = null;
    document.body.classList.remove('resizing');
  };
  sash.addEventListener('pointerup', end);
  sash.addEventListener('pointercancel', end);
  sash.addEventListener('dblclick', () => {
    const pane = paneOf();
    try { localStorage.removeItem(pane.pref); } catch (_) { /* private mode */ }
    setPane(pane, pane.fallback, false);
  });
  sash.addEventListener('keydown', (event) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    const pane = paneOf();
    const now = parseFloat(getComputedStyle($('main')).getPropertyValue(pane.variable)) || pane.fallback;
    const grow = (event.key === 'ArrowRight') === (pane === PANES.tree) ? 16 : -16;
    setPane(pane, now + grow, true);
    event.preventDefault();
  });
  window.addEventListener('resize', applyPanes);
  applyPanes();
}

// The Table's inspector (Zach, 2026-10-23): folded by default so the table gets the full width; a row click slides
// it in, a click back into the table (the selected row again, or the table's empty space), Escape or its close
// button folds it; the toolbar toggle or `]` pins it open (remembered).
const inspectorShown = () => S.inspPinned || S.inspOpen;
function setInspector(open) { S.inspOpen = open; render(); }
function setPinned(pinned) {
  S.inspPinned = pinned;
  S.inspOpen = pinned;
  writePref(PREFS.inspectorPinned, pinned ? '1' : '0');
  render();
}

// --------------------------------------------------------------------- the Prototype picker ----
// TEMPORARY (Zach's rule: a variant gated for review gets a bottom-right drop-down, never a URL flag): the detail
// layout (detail/*.js) and the utility menu (utilities/*.js) switch live and are remembered.
const layouts = () => window.IODetailLayouts || {};
const menus = () => window.IOUtilityMenus || {};
const runViews = () => window.IORunViews || {};
const chartViews = () => window.IOChartViews || {};
const registryIds = (registry, prefix) => Object.keys(registry).filter((id) => registry[id] && typeof registry[id][prefix] === 'function')
  .sort((a, b) => ((registry[a].order ?? 50) - (registry[b].order ?? 50)) || a.localeCompare(b));
function setupPrototype(layoutDefault, menuDefault, runsDefault, chartsDefault) {
  const wire = (select, registry, method, pref, fallback, key) => {
    const ids = registryIds(registry, method);
    const stored = readPref(pref);
    S[key] = ids.includes(stored) ? stored : ids.includes(fallback) ? fallback : ids[0] || null;
    // WHY the run view shows only its id ("r1"): the bar's room is limited and the skin switcher beside it must stay in reach;
    // its name and description are the option's tooltip
    const label = (id) => (key === 'runView' || key === 'chartView' ? esc(id) : `${esc(id)} · ${esc(registry[id].name || id)}`);
    const tip = (id) => (key === 'runView' || key === 'chartView' ? `${registry[id].name || id}: ${registry[id].description || ''}` : registry[id].description || '');
    select.innerHTML = ids.map((id) => `<option value="${esc(id)}" title="${esc(tip(id))}">${label(id)}</option>`).join('');
    if (key === 'runView') select.title = 'Runs view: how a running utility shows its terminal (r1 · Terminal panel with tabs ... r5)';
    if (key === 'chartView') select.title = 'Chart view: how a sparkline enlarges (c0 · nothing ... c5)';
    select.value = S[key] || '';
    select.addEventListener('change', () => {
      if (!registry[select.value]) return;
      S[key] = select.value;
      writePref(pref, select.value);
      if (key === 'layout') S.layoutChoice = select.value; // an explicit pick wins on every entity, the Computer too
      if (key === 'runView') mountRunView();
      if (key === 'chartView') window.IOCharts.select(S.chartView);
      render();
    });
    return ids.length;
  };
  const shown = wire($('detail-layout'), layouts(), 'render', PREFS.layout, layoutDefault || DEFAULTS.layout, 'layout')
    + wire($('utility-menu'), menus(), 'html', PREFS.menu, menuDefault || DEFAULTS.menu, 'menu')
    + wire($('run-view'), runViews(), 'mount', PREFS.runView, runsDefault || DEFAULTS.runs, 'runView')
    + (window.IOCharts ? wire($('chart-view'), chartViews(), 'mount', PREFS.chartView, chartsDefault || DEFAULTS.charts, 'chartView') : 0);
  // no stored layout: the per-entity default (layoutFor) decides, and the picker shows what it chose
  S.layoutChoice = registryIds(layouts(), 'render').includes(readPref(PREFS.layout)) ? readPref(PREFS.layout) : null;
  $('proto-switch').hidden = shown < 4; // one layout, one menu and one run view: nothing to switch
  mountRunView();
  if (window.IOCharts) { window.IOCharts.mount(makeChartsCtx()); window.IOCharts.select(S.chartView); }
  try { S.closed = new Set(JSON.parse(readPref(PREFS.sections) || '[]')); } catch (_) { S.closed = new Set(); }
  try { S.opened = new Set(JSON.parse(readPref(PREFS.sectionsOpen) || '[]')); } catch (_) { S.opened = new Set(); }
}
// The detail layout for the selection: the picker's explicit choice, else d4 for the Computer and d0 for the rest.
function layoutFor(isComputer) {
  if (S.layoutChoice && layouts()[S.layoutChoice]) return S.layoutChoice;
  const id = isComputer ? DEFAULTS.computerLayout : DEFAULTS.layout;
  return layouts()[id] ? id : (layouts()[DEFAULTS.layout] ? DEFAULTS.layout : registryIds(layouts(), 'render')[0] || null);
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
  // the header counts are web/summary.js's: stale, unknown, warn, error, increasing severity, one meaning per mark
  return `<div class="tree-head"><span>Entities · ${S.data.summary.entities}</span><span>${IOSummary.html(S.data.summary)}</span></div>`
    + visibleRows(S.built.rows).map((row) => rowHtml(row, true)).join('');
}

// ------------------------------------------------------------------------------ table mode --
function fmtSeen(row) {
  if (row.age_s === null || row.age_s === undefined) return '<span class="muted">—</span>';
  const period = row.expected_period_s ? ` / ${row.expected_period_s} s` : '';
  return `<span title="last received ${esc(row.last_received)}">${ago(row.age_s)} ago${period}</span> ${row.stale ? shape('stale', 'stale') : ''}`;
}

// Rate column (Zach, 2026-10-22: "they should also show stuff like Frequency"): records per second the host actually received
// for this row against what its records promised (expected_period_s), "0.20 /s · expected 0.20". WHY a quiet note, not a colour:
// a row that stopped arriving is already stale, which is the health signal; a slow-but-alive one just says so.
const fmtRate = (value) => (value === 0 ? '0' : value >= 0.1 ? value.toFixed(2) : value.toPrecision(2));
function rateCell(row) {
  const rate = row.rate;
  if (!rate) return '<td class="num"></td>';
  const expected = `expected ${fmtRate(rate.expected_per_s)}`;
  if (rate.received_per_s === null || rate.received_per_s === undefined) {
    return `<td class="num ratecell" title="measuring: the host judges a rate after watching 3 of its periods">— <span class="ratenote">${esc(expected)}</span></td>`;
  }
  const slow = rate.received_per_s < rate.expected_per_s / 2;
  const tip = `${rate.received_per_s} records/s received over the last ${rate.window_s} s; promised ${rate.expected_per_s} records/s (expected_period_s ${row.expected_period_s} s)`
    + `${slow ? ': under half of what it promised' : ''}`;
  return `<td class="num ratecell" title="${esc(tip)}">${esc(fmtRate(rate.received_per_s))} /s <span class="ratenote">· ${esc(expected)}</span>`
    + `${slow ? ' <span class="ratenote slow">· slow</span>' : ''}</td>`;
}

// Message column: the row's own words; a problem row's own report reads in full text colour, everything else quiet.
function messageCell(row) {
  const own = row.status && row.status.own;
  const word = own ? `<span class="lvword lv-${own}${row.knock_on_of ? ' knock' : ''}">${esc(own)}</span> ` : '';
  return `<td title="${esc(row.message)}" class="${own && !row.knock_on_of ? '' : 'quiet'}">${word}${esc(usb(row.message || ''))}</td>`;
}

// Below column: the advisory hint (a worse cause below) as the API sent it; quiet text, small shape.
function belowCell(row) {
  const hint = row.status && row.status.hint;
  if (!hint) return '<td></td>';
  return `<td title="${esc(hint.message)}"><span class="belowcell">${shape(hint.level, `below: ${hint.level}`)}<span>${esc(usb(hint.origin_name || hint.message || ''))}</span></span></td>`;
}

// WHY a minimum: the Message column is the one that says what is wrong; with the detail pane open at 1600 px and a
// structure's extra columns, a fixed layout gave it what was left (Zach's screenshot: "ME"). The table now grows
// past the pane and scrolls sideways instead.
const MESSAGE_MIN_PX = 280;
// WHY Rate beside Message (Zach, 2026-10-22: "right now they just show message but they should also show stuff like Frequency"): it is read with it
const FIXED_COLUMNS = [['Name', 380], ['ID', 170], ['Message', null], ['Rate', 230], ['Below', 190]];
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
      + rateCell(row)
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
    + `<thead><tr><th>Name</th><th>ID</th><th class="msgcol">Message</th><th>Rate</th><th>Below</th>${extraHeads}<th>Last seen</th><th>Checks</th><th>Faults</th></tr></thead>`
    + `<tbody>${body}</tbody></table>`;
}

// --------------------------------------------------------------------------- activity tab --
// WHY the tag is neutral unless the event is a diagnostic: warn / error colours mean a true diagnostic result and
// nothing else (Zach, 2026-10-23), so "plugged", "unplugged", "boot" read as plain words; an event the host logged
// at warn / error (a fault confirmed, a bus going error-passive) takes its severity's tint.
function eventRow(event) {
  const diagnostic = PROBLEM.has(event.severity);
  return `<div class="ev${diagnostic ? ` sev-${esc(event.severity)}` : ''}" data-go="${esc(event.path)}"${diagnostic ? ' data-diagnostic' : ''}><span class="t">${esc(event.time)}</span>`
    + `<span><span class="kind ${esc(event.kind.replace(/ /g, '-'))}">${esc(event.kind)}</span></span><span class="p">${esc(event.port)}</span>`
    + `<span title="${esc(event.message)}">${esc(usb(event.message))}</span><span class="src" title="${esc(event.source)}">${esc(event.severity === 'info' ? '' : event.severity)}</span></div>`;
}

// The Activity view is activity/log.js (docs/activity-log-v1.md): one calm table of the host's folded events, newest first,
// with its own DOM so its search box, scroll position and pause survive this page's once-a-second render. The host's fault
// memory, its runs and its kernel lines all arrive through GET /api/v1/activity?fold=1; link health moved to the Computer's
// detail (detail section "links"). The page only shows and hides it.
function renderActivityView() {
  if (!window.IOActivityLog) { $('activity-host').textContent = 'The activity log (activity/log.js) did not load.'; return; }
  window.IOActivityLog.setVisible(true);
  window.IOActivityLog.main($('activity-host'));
}

// --------------------------------------------------------------------------------- detail ----
// The detail panel is drawn by the active DETAIL LAYOUT (window.IODetailLayouts[id], detail/*.js; d0 = sections top to
// bottom). The page hands it the /api/v1/node JSON and a ctx of building blocks (docs/round2-seams.md §6): each
// ctx.parts.<name>() returns one section's BODY ('' = nothing to show), ctx.section() wraps a body in a collapsible
// header remembered per section id, ctx.draw() writes the panel only when it changed (focus and caret kept).
const kv = (rows) => {
  const body = rows.filter(Boolean).map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`).join('');
  return body ? `<div class="kv">${body}</div>` : '';
};

// One collapsible section. WHY sections and not a card grid: Zach, 2026-10-23: "the eye easily reads top to bottom
// not in these grid patterns"; a folded header stays folded on every entity (per section id) until reopened.
// opts.defaultOpen: false starts the section folded until the viewer opens it (remembered too, per section id).
const sectionOpen = (id, byDefault = true) => (S.closed.has(id) ? false : S.opened.has(id) ? true : byDefault);
function section(id, title, body, opts = {}) {
  if (!body) return '';
  const open = sectionOpen(id, opts.defaultOpen !== false);
  return `<section class="sec ${esc(id)}" data-sec="${esc(id)}"><h3 class="sec-h"><button class="sec-t" data-sec-toggle="${esc(id)}" aria-expanded="${open}">`
    + `<svg class="ic sec-chev"><use href="#i-chev"/></svg><span>${esc(title)}</span></button>${opts.right ? `<span class="right">${opts.right}</span>` : ''}</h3>`
    + `${open ? `<div class="sec-b">${body}</div>` : ''}</section>`;
}
function toggleSection(id, wasOpen) {
  if (wasOpen) { S.closed.add(id); S.opened.delete(id); } else { S.closed.delete(id); S.opened.add(id); }
  writePref(PREFS.sections, JSON.stringify([...S.closed]));
  writePref(PREFS.sectionsOpen, JSON.stringify([...S.opened]));
  render();
}

// One diagnostic (a check) read top to bottom: name and level, the message, its values, the quiet received line.
function checkBlock(check) {
  const values = check.values.map((v) => `<tr><td class="k">${esc(v.key)}</td><td>${esc(v.value)}</td></tr>`).join('');
  const measurements = check.measurements.map((m) => `<tr><td class="k">${esc(m.key)}</td><td>${shape(m.level)} ${esc(m.text)}</td></tr>`).join('');
  const timing = check.expected_period_s ? `every ${check.expected_period_s} s · STALE after ${check.stale_after_s} s` : 'event-driven (never stale)';
  return `<div class="check"><div class="check-h"><span class="check-name">${esc(check.name)}</span><span class="lvl">${pill(check.effective_level)}</span></div>`
    + `<div class="msg">${esc(usb(check.message || '—'))}${check.code ? ` <span class="mono muted">${esc(check.code)}</span>` : ''}</div>`
    + (values || measurements ? `<table class="vt">${measurements}${values}</table>` : '')
    + `<div class="meta">received ${esc((check.received_iso || '').slice(11, 19) || '—')} · ${ago(check.age_s)} ago · ${esc(timing)}`
    + `${check.level !== check.effective_level ? ` · reported ${esc(check.level)}` : ''}<br>${esc(check.source)}</div></div>`;
}

const partItem = (part) => `<div class="part-row" data-go="${esc(part.entity)}" title="${esc(part.name)}">${icon(part.icon)}`
  + `<span class="pname">${esc(part.label)}</span><span class="pmsg">${esc(usb(part.message))}</span>`
  + `<span class="st${part.knock_on_of ? ' knock' : ''}">${shape(part.problem, part.message)}</span></div>`;

// Link health (the computer diagnoses its own wires): the Computer's own section since round 3, on the host entity and
// on a structure's "Computer" grouping row (P1).
function linkHealthTable() {
  const rows = (S.act && S.act.link_health) || [];
  if (!rows.length) return '';
  return `<table class="lh"><colgroup><col style="width:34%"><col style="width:52px"><col style="width:22%"><col><col style="width:20%"></colgroup>`
    + '<thead><tr><th>Edge</th><th>Kind</th><th>Metric</th><th>Value</th><th>Source</th></tr></thead><tbody>'
    + rows.map((r) => `<tr data-go="${esc(r.path)}"><td title="${esc(r.edge)}">${esc(r.edge)}</td><td>${esc(r.kind)}</td><td title="${esc(r.metric)}">${esc(r.metric)}</td>`
      + `<td title="${esc(r.value)}"><span class="lhst">${shape(r.level)}</span>${esc(r.value)}</td><td title="${esc(r.source)}">${esc(r.source)}</td></tr>`).join('')
    + '</tbody></table>';
}

const HELPERS = { esc, usb, icon, shape, pill, ago, kv, statusMark, eventRow, checkBlock,
  // the Utilities tab draws what the menu draws (the same icon, label, state and reason) and its buttons are the menu's
  util: {
    icon: (name) => utilIcon(name), label: (action) => utilLabel(action), flow: (action) => flowOf(action),
    reason: (action) => reasonText(action), runnable: (action) => RUNNABLE.has(action.state),
    busy: (action) => (S.node ? utilityContext([action], S.node.node.path).busy(action) : ''),
  } };

// What a detail layout draws from, for the current selection.
function detailContext(byPath) {
  const group = !S.sel && S.selKey && S.built && S.built.byKey.has(S.selKey) ? S.built.byKey.get(S.selKey) : null;
  const node = group ? null : (byPath.get(S.sel) || (nodeCache.get(S.sel) || {}).row || S.data.tree);
  // WHY the kept answer only here: S.node stays "the host's answer for the selection" (the Utilities menu, the 3D drawer and the runs act on it); a page
  // already visited draws from what it last said, at once, until that answer lands
  const detail = node && (S.node && S.node.node.path === node.path ? S.node : notRunSplit((nodeCache.get(node.path) || {}).node) || null);
  // the row that stands for exactly this entity (not its owner, when the selection is a folded part)
  const rowKey = node && S.built ? S.built.byEntity.get(node.path) : null;
  const row = rowKey && S.built.byKey.get(rowKey).entity === node.path ? S.built.byKey.get(rowKey) : null;
  const isComputer = Boolean((node && node.kind === 'host') || (group && /(^|:)computer$/.test(group.key)));
  const parts = {
    head() {
      if (group) {
        return `<div class="d-head"><div class="d-title">${icon(group.icon)}<span>${esc(group.label)}</span></div>`
          + `<div class="d-msg">${group.status.hint ? `${esc(group.status.hint.level)} below · ${esc(usb(group.status.hint.message))}` : esc(usb(group.message || 'grouping row'))}</div></div>`;
      }
      const title = row ? row.label : usb(node.name);
      const hint = node.hint ? `<span class="muted"> · below: <a class="link" data-go="${esc(node.hint.origin)}">${esc(node.rollup.origin_name)}</a> is ${esc(node.hint.level)}</span>` : '';
      return `<div class="d-head"><div class="d-title">${icon(node.icon)}<span>${esc(title)}</span>${pill(node.level)}</div>`
        + `<div class="d-msg" title="${esc(node.message || '')}">${esc(usb(node.message || '—'))}${hint}</div>`
        + `<div class="d-path">${title !== node.name ? `${esc(node.name)} · ` : ''}${esc(node.path)}${node.hardware_id ? ` · ${esc(node.hardware_id)}` : ''}</div></div>`;
    },
    banners() {
      if (!node) return '';
      let html = loadNote(node);
      if (node.knock_on_of) {
        const cause = byPath.get(node.knock_on_of);
        html += `<div class="banner knock">↳ <span>Explained by <a class="link" data-go="${esc(node.knock_on_of)}">${esc(cause ? cause.name : node.knock_on_of)}</a> above: counted once, as that cause.</span></div>`;
      }
      if (node.stale) html += `<div class="banner stale" data-diagnostic>◷ <span><b>Stale:</b> nothing received for ${ago(node.age_s)} (expected every ${esc(node.expected_period_s)} s). The last values stay visible with their age.</span></div>`;
      if (!node.connected) html += '<div class="banner osonly">Not connected.</div>';
      return html;
    },
    whatToCheck: () => (detail ? whatToCheckBody(detail, row) : ''),
    // (round 3.5: no `utilities` part: the utilities live only in the entity header's Utilities ▾, drawn by the shell)
    setup: () => (detail ? setupBody(detail) : ''),
    status() {
      if (!node) return '';
      return kv([
        ['shows', `${pill(node.level)} <span class="muted">${node.own.level === null ? 'grouping: worst below' : 'its own self-report'}</span>`],
        ['worst below', node.rollup.level ? `${pill(node.rollup.level)} ${node.rollup.origin && node.rollup.origin !== node.path ? `<a class="link" data-go="${esc(node.rollup.origin)}">${esc(node.rollup.origin_name)}</a>` : ''}` : '<span class="muted">nothing reports</span>'],
        Object.keys(node.rollup.counts).length ? ['causes below', Object.entries(node.rollup.counts).map(([l, n]) => `${n} ${l}`).join(' · ')] : null,
        node.rollup.knock_on.length ? ['knock-on', `${node.rollup.knock_on.length} collapsed`] : null,
        ['last received', node.age_s === null ? '<span class="muted">no records of its own</span>' : `${ago(node.age_s)} ago`],
        ['owner', esc(node.owner || '—')],
      ]);
    },
    identity() {
      if (!node || !detail) return '';
      const idRows = Object.entries(node.identity || {}).map(([k, v]) => [k.replace('_', ' '), esc(v)]);
      const parent = detail.ancestors[detail.ancestors.length - 1];
      if (parent) idRows.push([node.edge === 'part_of' ? 'part of' : 'via', `<a class="link" data-go="${esc(parent.path)}">${esc(parent.name)}</a>`]);
      if (node.hardware_id) idRows.push(['hardware id', `<span class="mono">${esc(node.hardware_id)}</span>`]);
      return kv(idRows);
    },
    // the layers this row is reached through (a structure marked them folded: 'path') and its composition parts
    connection: () => ((row && row.parts) || []).filter((part) => part.folded === 'path').map(partItem).join(''),
    parts: () => ((row && row.parts) || []).filter((part) => part.folded !== 'path').map(partItem).join(''),
    checks: () => (detail ? detail.checks.map(checkBlock).join('') : ''),
    faults() {
      if (!detail || !detail.faults.length) return '';
      return kv(detail.faults.map((f) => [f.code, `${pill(f.severity)} ${esc(f.status)}${f.active ? ' · active' : ''} · ${f.occurrence_count}× · ${esc(f.message)}`]));
    },
    events: () => (detail ? (detail.events.map(eventRow).join('') || '<div class="muted">No events.</div>') : ''),
    links: () => (isComputer ? linkHealthTable() : ''),
    members() {
      if (!group) return '';
      return group.children.map((child) => `<div class="part-row" ${child.entity ? `data-go="${esc(child.entity)}"` : ''}>${icon(child.icon)}`
        + `<span class="pname">${esc(child.label)}</span><span class="pmsg">${esc(usb(child.message || ''))}</span>${statusMark(child)}</div>`).join('') || '<div class="muted">none</div>';
    },
  };
  const counts = {
    connection: ((row && row.parts) || []).filter((part) => part.folded === 'path').length,
    parts: ((row && row.parts) || []).filter((part) => part.folded !== 'path').length,
    checks: detail ? detail.checks.length : 0,
    members: group ? group.children.length : 0,
  };
  return { node, detail, row, group, isComputer, counts, byPath, state: S.data, activity: S.act, view: S.view, parts, section,
    isOpen: (id, byDefault = true) => sectionOpen(id, byDefault), h: HELPERS, draw: drawDetail,
    // the drill seam: the open diagnostic {name, path, measurement} (or null) and a layout's own sub-page id (or null);
    // each open / close is one browser-history entry (back / forward, the mouse's buttons)
    diagnostic: group ? null : diagnosticOf(), page: group ? null : S.drill.page || null, openDiagnostic, openPage, closeDiagnostic,
    // the shared history cache: an entry {data, error, loading, at}, data null until the first answer lands
    history: (path, opts = {}) => historyOf(path, opts), bar: (path, bins = 60, opts = {}) => barOf(path, bins, opts),
    settings: S.settings, redraw: redrawDetail };
}

// Write the detail panel only when its HTML changed: the dry-run panel and the What to check list are read, not
// glanced at (selection, focus and scroll stay). set_id's new-id box and Retire…'s reason box are rewritten with the
// detail as the person types: keep the focus in it and the caret at its end (a number input has no selection API;
// re-setting its value moves the caret there).
// Make `from`'s children equal `to`'s, keeping every node that did not change: a hovered row keeps its hover and its
// tooltip, an open text box its caret. WHY not innerHTML: the detail's ages ("2 s ago") change every second, and a
// whole rewrite restarted a utility's hover tooltip each tick (it flickered, round 3).
function morph(from, to) {
  const old = [...from.childNodes];
  const fresh = [...to.childNodes];
  fresh.forEach((node, at) => {
    const current = old[at];
    if (!current) { from.appendChild(node); return; }
    if (current.isEqualNode(node)) return;
    if (current.nodeType === 3 && node.nodeType === 3) { current.nodeValue = node.nodeValue; return; }
    if (current.nodeType === 1 && current.nodeName === node.nodeName && !(current instanceof SVGElement)) {
      for (const { name } of [...current.attributes]) if (!node.hasAttribute(name)) current.removeAttribute(name);
      for (const { name, value } of [...node.attributes]) if (current.getAttribute(name) !== value) current.setAttribute(name, value);
      morph(current, node);
      return;
    }
    from.replaceChild(node, current);
  });
  for (const extra of old.slice(fresh.length)) extra.remove();
}

// The shell's own part of every entity page, whatever the layout drew: the Utilities ▾ button at the top-right of the
// page's header (`.d-head`), its menu and its dry-run sheet anchored under it, and the quiet run line right under the
// header. WHY drawn here and not by a layout: Zach, 2026-10-23: "have that drop down button in the header of the entity
// page ... so I always know where it is. It makes sense to have it in a different location from where the rest of the
// things are" — one place for every entity and every layout, and the utilities nowhere else.
function decorate(root, extra) {
  let head = root.querySelector('.d-head');
  if (!head) { // a layout that drew no header (an error): the tools still sit at the top
    head = document.createElement('div');
    head.className = 'd-head';
    root.prepend(head);
  }
  head.classList.add('has-tools');
  const box = document.createElement('div');
  box.innerHTML = extra.tools;
  head.appendChild(box.firstElementChild);
  if (extra.run) {
    box.innerHTML = extra.run;
    head.after(...box.childNodes);
  }
}

function drawDetail(html) {
  const extra = { tools: entityTools(), run: runLine() };
  const key = `${html}\u0000${extra.tools}\u0000${extra.run}`;
  if (key === S.detailHtml) return;
  const typing = document.activeElement && document.activeElement.matches('[data-util-newid], [data-setup-reason]')
    ? (document.activeElement.matches('[data-setup-reason]') ? '[data-setup-reason]' : '[data-util-newid]') : null;
  const next = document.createElement('div');
  next.innerHTML = html;
  decorate(next, extra);
  morph($('detail'), next);
  S.detailHtml = key;
  const box = typing && $('detail').querySelector(typing);
  if (box) { const typed = box.value; box.focus(); box.value = ''; box.value = typed; }
}

// the drill step the detail shows (its URL without the view): each keeps its own scroll position
const drillAt = () => navUrl({ ...navNow(), view: null });
function renderDetail(byPath) {
  // WHY: hidden inspectors/Activity panes keep their data, but do no detail/chart/watch work.
  if (document.hidden || !$('detail').checkVisibility()) {
    const hiddenLayout = layouts()[S.layout];
    if (hiddenLayout && hiddenLayout.pause) hiddenLayout.pause();
    return;
  }
  const ctx = detailContext(byPath);
  const previousLayout = S.layout;
  S.layout = layoutFor(ctx.isComputer);
  if (previousLayout !== S.layout && layouts()[previousLayout] && layouts()[previousLayout].pause) layouts()[previousLayout].pause();
  const picker = $('detail-layout');
  if (S.layout && picker.value !== S.layout && document.activeElement !== picker) picker.value = S.layout;
  const layout = layouts()[S.layout];
  if (!layout) {
    drawDetail(`${ctx.parts.head()}<div class="d-body"><div class="err">No detail layout loaded (detail/*.js).</div></div>`);
    return;
  }
  try {
    layout.render($('detail'), ctx.detail, ctx);
  } catch (error) {
    console.error(`detail layout ${S.layout}:`, error);
    drawDetail(`${ctx.parts.head()}<div class="d-body"><div class="err">detail layout ${esc(S.layout)} failed: ${esc(error)}</div></div>`);
  }
  // a new drill step starts at the top; one the viewer comes back to (back / forward) where they left it
  const at = drillAt();
  if (at !== S.drawnAt && (ctx.detail || ctx.group)) {
    $('detail').scrollTop = S.scrolls.get(at) || 0;
    S.drawnAt = at;
  }
}

// ------------------------------------------------------------------ utilities (the action buttons) ----
// The printer-utility window (Zach, 2026-10-22: Canon "Cleaning": icon, title, one-line description per utility),
// over docs/actions-vocabulary-v0.1.md. Checks first (query), then maintenance (changes / interrupts), then motion
// (moves) set apart, then the driver's own non-standard actions under "More".
// WHY a click runs, or opens one Run page (Zach, 2026-10-29: "There are way too many steps to use a utility"): an
// immediate utility (a query, Open console, Stop telemetry) runs on the click; any other opens the Run page (renderModal)
// and runs on its Run button. The server still runs a real action only with a dry run's token, from this page
// (io_diagnostics/server.py, docs/api-v1.md): the page asks for it behind the click.
const STANDARD_ORDER = ['check_comms', 'check_firmware', 'update_firmware', 'update_config', 'clear_faults', 'set_id', 'reboot', 'reset', 'reset_port',
  'stop_all', 'open_console', 'calibrate', 'identify'];
const STANDARD = new Set(STANDARD_ORDER);
// The host's own reset_port and reset ask twice when something else has the device open (docs/usb-reset.md): the dry run's
// `second_confirm` names who loses it; the Run page states it, and its Run button confirms it (confirm_holders).
const secondConfirmOf = (util) => String(((util && util.dry && util.dry.result && util.dry.result.action_result) || {}).second_confirm || '');
// WHY only "ready" runs (Zach, 2026-10-29): "The utilities menu should just have 2 states: able to run: just the name of the
// utility; OR not able to run: greyed out with an explanation underneath as to why." The host never answers "unknown" now
// (docs/api-v1.md, "Actions: two states"); blocked / waiting / nothing_to_do are greyed with their reason.
const RUNNABLE = new Set(['ready']);
// WHY one click for these (Zach, 2026-10-29: "Some things, like Open console (tview), should just run with a single button
// click. As ergonomic and fast as possible"): nothing to enter, nothing moves, nothing stored on the board changes. Every
// other utility opens ONE Run page (the confirm modal) whose Run button runs it: "at most 1 other page".
const IMMEDIATE = new Set(['open_console', 'stop_telemetry']);
const flowOf = (action) => ((action.danger === 'query' || IMMEDIATE.has(baseName(action.name))) && action.name !== 'set_id'
  && !action.second_confirm ? 'immediate' : 'confirm');
// the label in the menu and the tab: a utility that asks first keeps its "…", one that runs on the click has none
const utilLabel = (action) => `${plainTitle(action.title)}${flowOf(action) === 'confirm' ? '…' : ''}`;
// the one line under a greyed utility: the host's reason without its "blocked: " lead
const reasonText = (action) => String(action.reason || action.state || '').replace(/^blocked:\s*/, '');
const UTIL_ICONS = {
  check_comms: '<path d="M4.9 19.1a10 10 0 0 1 0-14.2M7.8 16.2a6 6 0 0 1 0-8.4M16.2 7.8a6 6 0 0 1 0 8.4M19.1 4.9a10 10 0 0 1 0 14.2"/><circle cx="12" cy="12" r="2"/>',
  check_firmware: '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
  update_firmware: '<path d="M12 3v12M7 10l5 5 5-5M5 21h14"/>',
  update_config: '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/><circle cx="15" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
  clear_faults: '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
  set_id: '<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18"/>',
  reboot: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  reset: '<path d="M3 12a9 9 0 0 1 15.4-6.4L21 8M21 3v5h-5M21 12a9 9 0 0 1-15.4 6.4L3 16M3 21v-5h5"/>',
  reset_port: '<path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0ZM12 17v5"/>',
  stop_all: '<path d="M7.9 2h8.2L22 7.9v8.2L16.1 22H7.9L2 16.1V7.9Z"/><rect x="9" y="9" width="6" height="6" rx="1"/>',
  open_console: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3M11 15h6"/>',
  calibrate: '<circle cx="12" cy="12" r="8"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/><circle cx="12" cy="12" r="1.5"/>',
  identify: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/><rect x="2" y="5" width="20" height="14" rx="3"/>',
};
// a composite's batch (all:<action>, docs/composition-v1.md section 6) is drawn, grouped and ordered as the action it repeats
const baseName = (name) => String(name).replace(/^all:/, '');
const utilIcon = (name) => `<svg class="u-ic" viewBox="0 0 24 24" aria-hidden="true">${UTIL_ICONS[STANDARD.has(baseName(name)) ? baseName(name) : 'more']}</svg>`;
const plainTitle = (title) => String(title || '').replace(/…$/, '');

// WHY Tools: a vendor's own console (tview, realsense-viewer) is something a person looks for by name (Zach, 2026-10-24: he
// did not find "Open console"); it sits right after Checks, outside Maintenance and never in Motion.
const TOOLS = new Set(['open_console']);
// the Configuration tab's three writes are driven by its edit session (docs/state-config-v1.md section 9), never from a menu
const CONFIG_WRITES = new Set(['set_config', 'persist_config', 'reset_config']);
function utilityGroups(allActions) {
  const actions = allActions.filter((action) => !CONFIG_WRITES.has(action.name));
  const order = (action) => (STANDARD.has(baseName(action.name)) ? STANDARD_ORDER.indexOf(baseName(action.name)) : STANDARD_ORDER.length);
  const sorted = [...actions].sort((a, b) => order(a) - order(b));
  const standard = sorted.filter((action) => STANDARD.has(baseName(action.name)));
  return [
    { id: 'checks', title: 'Checks', items: standard.filter((action) => action.danger === 'query' && !TOOLS.has(action.name)) },
    { id: 'tools', title: 'Tools', items: standard.filter((action) => TOOLS.has(action.name)) },
    { id: 'maintenance', title: 'Maintenance', items: standard.filter((action) => action.danger !== 'query' && action.danger !== 'moves' && !TOOLS.has(action.name)) },
    { id: 'motion', title: 'Motion · makes hardware move', items: standard.filter((action) => action.danger === 'moves') },
    { id: 'more', title: 'More', items: sorted.filter((action) => !STANDARD.has(baseName(action.name))) },
  ].filter((group) => group.items.length);
}

// The CAN id a node-scoped action names: the entity's own last path segment ("…/can/15" -> 15), as the host's {id}.
function nodeOf(path, action) {
  if (action.scope !== 'node') return null;
  const last = String(path).split('/').pop();
  return /^\d+$/.test(last) ? Number(last) : null;
}

// set_id's new CAN id, typed on its Run page: {id, problem}. id is a whole number 1-126 other than the board's own id and
// any other board this bus already shows (the driver checks the bus again before it writes); else null with the problem.
function newIdOf(path, action) {
  if (action.name !== 'set_id') return { id: null, problem: '' };
  const text = String(S.newIds.get(path) ?? '').trim();
  if (!text) return { id: null, problem: 'Type the new CAN id (1-126).' };
  if (!/^\d+$/.test(text) || Number(text) < 1 || Number(text) > 126) return { id: null, problem: 'A CAN id is a whole number from 1 to 126.' };
  const id = Number(text);
  if (id === nodeOf(path, action)) return { id: null, problem: `This board is CAN ${id} already.` };
  const bus = String(path).split('/').slice(0, -1).join('/');
  if (S.byPath && S.byPath.has(`${bus}/${id}`)) return { id: null, problem: `CAN ${id} is already on this bus.` };
  return { id, problem: '' };
}

async function postAct(body) {
  try {
    const response = await fetch('/api/v1/act', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify(body) });
    return { status: response.status, data: await response.json() };
  } catch (error) {
    return { status: 0, data: { refused: 'Unreachable', error: `the host did not answer: ${error}` } };
  }
}

// A click on a utility, from the header's menu or the Utilities tab: the SAME flow (Zach, 2026-10-29: "Unify the buttons on
// the utility page and the drop-down tray: they are basically the same buttons and should go to the same next page").
// An immediate one runs now; any other opens its Run page (the modal), which holds its inputs and its confirmations and one
// Run button. WHY the dry run is still made: the host runs a real action only with a dry run's token (docs/api-v1.md); the
// page asks for it behind the click (immediate) or while the Run page is open, so the person never sees or types it.
function startUtility(name) {
  const action = S.node && (S.node.actions || []).find((item) => item.name === name);
  if (!action || !RUNNABLE.has(action.state)) return;
  const path = S.node.node.path;
  if (S.util && S.util.path === path && ['dry', 'running'].includes(S.util.phase) && S.util.name === name) return;
  const util = { path, name, node: nodeOf(path, action), newId: null, phase: 'confirm', dry: null, result: null, error: '', refused: '',
    asking: false, title: action.title, danger: action.danger, owner: action.owner, label: entityLabel(path), flow: flowOf(action) };
  S.util = util;
  S.menuOpen = false;
  S.runs.delete(path);
  if (util.flow === 'immediate') {
    util.phase = 'dry';
    render();
    dryRun(util).then((ok) => {
      if (S.util !== util) return;
      if (ok) runUtility(); else finishUtility(util, 0, { refused: util.refused, error: util.error });
    });
    return;
  }
  // WHY asking before the first draw: drawn first, the Run page offered Run with no token yet, nothing drew again until the answer, and a press fell on `util.asking` in confirmRun and went
  // nowhere, without a word. A press on Run must never go nowhere.
  util.asking = name !== 'set_id';
  render();
  if (name !== 'set_id') dryRun(util).then(() => { if (S.util === util) render(); }); // set_id's token names its new id
}
// The tree's row menu and the page layouts start a utility by this name (layouts/host.js)
function startDryRun(name) { startUtility(name); }

// The dry run behind the click: true with util.dry holding the token, else false with util.refused / util.error saying why.
async function dryRun(util) {
  util.asking = true;
  const { status, data } = await postAct({ path: util.path, action: util.name, node: util.node, ...(util.newId !== null ? { new_id: util.newId } : {}), dry_run: true });
  util.asking = false;
  const result = (data && data.result) || {};
  if (status === 200 && data.token && !result.refused && result.outcome === 'ok') {
    util.dry = { ...data, newId: util.newId };
    util.at = Date.now();
    util.refused = '';
    util.error = '';
    return true;
  }
  util.dry = null;
  util.refused = result.refused || data.refused || (status === 200 ? result.outcome || 'no token' : `HTTP ${status}`);
  util.error = result.message || result.stderr_tail || data.error || '';
  return false;
}

// The Run page's Run button: the one more click. A token older than 4 min (its window is 5) or for another new id is asked
// for again first.
async function confirmRun() {
  const util = S.util;
  if (!util || util.phase !== 'confirm' || util.asking) return;
  const action = S.node && (S.node.actions || []).find((item) => item.name === util.name);
  if (!action || !RUNNABLE.has(action.state)) return;
  if (util.name === 'set_id') {
    const typed = newIdOf(util.path, action);
    if (typed.id === null) return;
    util.newId = typed.id;
  }
  const fresh = util.dry && util.dry.token && util.dry.newId === util.newId && Date.now() - util.at < 240000;
  if (!fresh) {
    render();
    const ok = await dryRun(util);
    if (S.util !== util) return;
    if (!ok) { render(); return; }
  }
  runUtility();
}

async function runUtility() {
  const util = S.util;
  if (!util || !util.dry || !util.dry.token || util.phase === 'running') return;
  util.phase = 'running';
  util.startedAt = Date.now();
  S.runDetails = false;
  render();
  // the run repeats the dry run's new id: the server's token is bound to it (a token for CAN 7 never runs CAN 9).
  // WHY detach: the run is a terminal session that may wait for a typed answer, so the page does not hold the request
  const { status, data } = await postAct({ path: util.path, action: util.name, node: util.node, ...(util.newId !== null ? { new_id: util.newId } : {}), ...(secondConfirmOf(util) ? { confirm_holders: true } : {}), token: util.dry.token, detach: true });
  if (status === 202 && (data.run || data.runs) && window.IORuns) {
    // WHY no "is it still the open one" return: the run is going; its end (syncRunUtilities) belongs to its entity's run
    // line (kept in S.runs while the viewer looks elsewhere) and to the Activity log either way
    const started = data.runs || [data.run]; // a batch (all:<action>) answers every run it created, all queued or running
    util.runId = started[0].id;
    util.runIds = data.runs ? started.map((run) => run.id) : null;
    window.IORuns.ingest(started);
    // a quiet query keeps the panel as the person left it; anything that changes or moves a device shows its terminal
    if (util.danger !== 'query') openRun(util.runId);
    render();
    return;
  }
  finishUtility(util, status, data);
}

// The utility is over: its result (a refusal, or the finished run's) becomes the run line. (The Activity log has the run
// as a row of its own: the host logs RunStarted / RunEnded, so the page no longer adds a line for it.)
function finishUtility(util, status, data) {
  util.phase = 'done';
  util.doneAt = Date.now();
  if (status === 200 && data.result) util.result = data.result; else { util.refused = data.refused || `HTTP ${status}`; util.error = data.error || ''; }
  S.actAt = 0; // the Activity log and the entity's diagnostics are read again now: the action may have changed them
  if (S.sel === util.path) { S.nodeAt = 0; loadNode(); }
  render();
}

// A batch's result is one line: how many of its runs finished, and the names of the ones that did not (a run the driver refused or
// that failed). Each run keeps its own terminal and result; this is only the entity's one-line status.
function batchResult(details) {
  const bad = details.filter((detail) => detail.state !== 'done' || (detail.result || {}).outcome !== 'ok'
    || ((detail.result || {}).records || []).some((record) => FAILED_CODES.has(record.code)));
  const names = bad.map((detail) => String(detail.label || detail.id).split(' · ')[0]);
  return { outcome: bad.length ? 'failed' : 'ok', returncode: 0, records: [], rejected: [], run_id: details[0].id, batch: true,
    duration_s: Math.round(details.reduce((sum, detail) => sum + ((detail.result || {}).duration_s || 0), 0) * 10) / 10,
    message: bad.length ? `${details.length - bad.length} of ${details.length} runs done · ${names.join(', ')} did not finish` : `${details.length} runs done` };
}

// A utility whose run has ended on the host: read its result once and finish it. Called after every poll.
function syncRunUtilities() {
  for (const util of [S.util, ...S.runs.values()]) {
    if (!util || util.phase !== 'running' || !util.runId || util.syncing) continue;
    const ids = util.runIds || [util.runId];
    if (ids.some((id) => { const run = window.IORuns.get(id); return !run || run.active; })) continue;
    util.syncing = true;
    Promise.all(ids.map((id) => window.IORuns.detail(id)))
      .then((details) => finishUtility(util, 200, { result: util.runIds ? batchResult(details) : { ...(details[0].result || {}), run_id: details[0].id } }))
      .catch((error) => finishUtility(util, 0, { refused: 'Unreachable', error: String(error) }));
  }
}

// ----------------------------------------------------------------------------------------- run views ----
// The ctx a run view draws from (docs/runs-v1.md section 7). One object; its members are live.
let runsCtx = null;
function makeRunsCtx() {
  const R = window.IORuns;
  const prefKey = (key) => `io-diagnostics.runs.${key}`;
  return {
    runs: R.list, run: R.get, onChange: R.onChange, stream: R.stream, sendInput: R.sendInput, interrupt: R.interrupt, resize: R.resize,
    terminal: R.attachTerminal, castUrl: R.castUrl,
    open: (id) => openRun(id),
    redraw: () => queueRedraw(),
    root: $('runs-root'),
    statusSlot: $('runs-status'),
    css(key, text) {
      if (document.querySelector(`style[data-runs-css="${key}"]`)) return;
      const style = document.createElement('style');
      style.dataset.runsCss = key;
      style.textContent = text;
      document.head.appendChild(style);
    },
    pref(key, fallback) { const value = readPref(prefKey(key)); return value === null || value === '' ? fallback : value; },
    setPref(key, value) { writePref(prefKey(key), value); },
    h: { esc, shape, plainTitle, elapsed: R.elapsed, stateWords: R.stateWords },
  };
}
// Mount the selected run view, after unmounting the one before it (the Prototype picker's third select).
let mountedRunView = null;
function mountRunView() {
  if (!window.IORuns) return;
  if (!runsCtx) runsCtx = makeRunsCtx();
  const next = runViews()[S.runView] && S.runView;
  if (mountedRunView === next) return;
  if (mountedRunView && runViews()[mountedRunView]) {
    try { runViews()[mountedRunView].unmount(runsCtx); } catch (error) { console.error(`run view ${mountedRunView} unmount:`, error); }
  }
  mountedRunView = null;
  if (!next) return;
  try {
    runViews()[next].mount(runsCtx);
    mountedRunView = next;
    // WHY: a mounted view gets its initial model even when a quiet SSE feed causes no subsequent full render.
    updateRunView();
  } catch (error) {
    console.error(`run view ${next} mount:`, error);
    $('runs-root').innerHTML = `<div class="err">run view ${esc(next)} failed: ${esc(error)}</div>`;
    $('runs-root').hidden = false;
  }
}
function updateRunView() {
  const view = mountedRunView && runViews()[mountedRunView];
  if (!view || !view.update) return;
  try { view.update(runsCtx); } catch (error) { console.error(`run view ${mountedRunView} update:`, error); }
}
// Show a run: the selected view's open(id).
function openRun(id) {
  const view = mountedRunView && runViews()[mountedRunView];
  if (view && view.open) {
    try { view.open(id, runsCtx); } catch (error) { console.error(`run view ${mountedRunView} open:`, error); }
  }
}
const canShowRun = () => Boolean(mountedRunView && runViews()[mountedRunView] && runViews()[mountedRunView].open);

// What a finished run says, in one line: ok + its own message ("Firmware updated to 1.1.2"), or why it failed.
function runOutcome(util) {
  if (!util.result) return { ok: false, text: `not run · ${util.refused}${util.error ? ` · ${util.error}` : ''}` };
  const result = util.result;
  const refusedByDriver = (result.records || []).find((record) => FAILED_CODES.has(record.code));
  if (!result.refused && result.outcome === 'ok' && !refusedByDriver) return { ok: true, text: result.message || `done in ${result.duration_s} s` };
  // WHY not a failure: the person pressed Stop; one quiet line, the host's own words for how it ended
  if (result.outcome === 'stopped') return { ok: true, stopped: true, text: `${plainTitle(util.title)} ${result.message || 'stopped'}` };
  const why = result.refused ? `refused: ${result.refused}` : refusedByDriver ? refusedByDriver.code : result.outcome;
  return { ok: false, text: `${why}${(result.message || (refusedByDriver && refusedByDriver.message)) ? ` · ${result.message || refusedByDriver.message}` : ''}` };
}

// The row label of an entity (moteus-c1 · CAN 15), else its API name.
function entityLabel(path) {
  const key = S.built && S.built.byEntity.get(path);
  const row = key && S.built.byKey.get(key);
  if (row && row.entity === path) return row.label;
  const node = S.byPath && S.byPath.get(path);
  return node ? usb(node.name) : path;
}

// A run of another entity is kept with its entity (S.runs) while the viewer looks elsewhere, and comes back with it.
function stashRun(path) {
  if (S.util && S.util.path !== path) {
    if (S.util.phase === 'running' || S.util.phase === 'done') S.runs.set(S.util.path, S.util);
    S.util = null;
  }
  if (path && !S.util && S.runs.has(path)) { S.util = S.runs.get(path); S.runs.delete(path); }
}

const commandOf = (answer) => {
  const command = answer && answer.command;
  if (Array.isArray(command) && command.length) return command.join(' ');
  if (typeof command === 'string' && command) return command;
  return '';
};

function stepsHtml(answer) {
  const steps = (answer && answer.steps) || [];
  if (!steps.length || isLadder(answer)) return '';
  return `<ol class="u-steps">${steps.map((step) => `<li><span>${esc(step.purpose)}</span> <span class="tag recorded">${esc(step.runs || '')}</span>`
    + `${step.argv && step.argv.length ? `<code>${esc(step.argv.join(' '))}</code>` : ''}`
    + `${step.sends && step.sends.length ? `<span class="muted"> · sends ${esc(step.sends.join(', '))}</span>` : ''}</li>`).join('')}</ol>`;
}

// The host's Reset… (docs/device-reset.md) answers its dry run with a ladder: every rung of the device's profile, in order, with
// whether it runs here and why not, why it sits at that place and what happens when it works. The Run page shows it in the open,
// rung by rung: what pressing Run will try is the point of that page. Calm on purpose: a skipped rung is muted, never a warning.
const isLadder = (answer) => ((answer && answer.steps) || []).some((step) => typeof step.runs === 'boolean' && 'title' in step && 'kind' in step);
function ladderHtml(answer) {
  if (!isLadder(answer)) return '';
  const rows = answer.steps.map((step) => `<li class="u-rung${step.runs ? '' : ' skip'}" data-rung="${esc(step.kind)}" data-runs="${step.runs ? 'yes' : 'no'}">`
    + `<div class="u-r-h"><span class="u-r-t">${esc(step.title)}</span><span class="tag recorded">${step.runs ? 'runs' : 'skipped'}</span></div>`
    + (step.runs ? '' : `<div class="u-r-skip">${esc(step.skip_why || 'not here')}</div>`)
    + `<div class="u-r-l"><span class="u-k">Why</span><span>${esc(step.why)}</span></div>`
    + `<div class="u-r-l"><span class="u-k">Expect</span><span>${esc(step.expect)}</span></div>`
    + (step.runs && step.available && step.available.why ? `<code>${esc(step.available.why)}</code>` : '') + '</li>').join('');
  return `<div class="m-pre-h">What it tries, in order, until it answers</div><ol class="u-ladder" data-ladder>${rows}</ol>`
    + (answer.fix ? `<div class="u-r-fix muted">${esc(answer.fix)}</div>` : '');
}

const FAILED_CODES = new Set(['ActionRefused', 'ConfirmationRequired', 'ConfirmRequired', 'ActionFailed']);
// The N runs of a batch, in the order they start: one line each (the board's name and the action, and when it starts: now, or behind how
// many earlier runs of its exclusive group), then the members the batch leaves out and why.
function batchRunsHtml(batch) {
  const skipped = (batch.skipped || []).map((item) => `${item.name} (${item.why})`);
  const queued = [...new Set(batch.runs.filter((run) => run.after > 0).map((run) => run.group))];
  const text = skipped.length > 3 ? `${skipped.length} members (${[...new Set((batch.skipped || []).map((item) => item.why))].join('; ')})` : skipped.join(', ');
  return `<div class="u-batch-h"><b>${esc(batch.n)} runs</b> · one confirm <span class="tag recorded">batch</span>`
    + `${queued.length ? ` <span class="muted">· runs of ${esc(queued.join(', '))} go one at a time</span>` : ''}</div>`
    + `<ol class="u-batch" data-batch-runs="${esc(batch.n)}">${batch.runs.map((run) => `<li><span class="u-b-n">${esc(run.order)}</span><span class="u-b-l">${esc(run.label)}</span>`
      + `<span class="u-b-s muted" title="${esc(run.starts)}">${run.after > 0 ? `after ${esc(run.after)}` : 'now'}</span></li>`).join('')}</ol>`
    + (skipped.length ? `<div class="u-b-skip muted">Not included: ${esc(text)}</div>` : '');
}

// "Check communication" -> "Checking communication", "Run calibration…" -> "Running calibration", "Stop all" ->
// "Stopping all": the busy line under a running utility (Zach's quiet "received 08:55:30 · 1 s ago" line, 2026-10-23)
const DOUBLED = new Set(['stop', 'set', 'reset', 'run', 'plan', 'get']);
function gerund(title) {
  const [verb, ...rest] = plainTitle(title).split(' ');
  if (!verb) return '';
  const lower = verb.toLowerCase();
  let ing;
  if (DOUBLED.has(lower)) ing = `${verb}${verb.slice(-1)}ing`;
  else if (/[^e]e$/i.test(verb)) ing = `${verb.slice(0, -1)}ing`;
  else ing = `${verb}ing`;
  return [ing, ...rest].join(' ');
}

// The ctx a utility menu draws from (window.IOUtilityMenus[id].html(ctx), docs/round2-seams.md §6). The page owns the
// whole flow: a [data-util=<name>] click runs an immediate utility at once and opens any other's Run page (the modal),
// the same for the menu and the Utilities tab.
function utilityContext(actions, path) {
  const active = S.util && S.util.path === path ? S.util : null;
  const isActive = (action) => Boolean(active && active.name === action.name);
  const phase = (action) => (isActive(action) ? active.phase : 'idle');
  return {
    actions,
    path,
    owner: (actions[0] && actions[0].owner) || '',
    active,
    groups: utilityGroups(actions),
    h: { esc, utilIcon, plainTitle, gerund, shape, utilLabel, reasonText },
    isActive,
    phase,
    runnable: (action) => RUNNABLE.has(action.state),
    flow: flowOf,
    // WHY none: the new id is typed on set_id's Run page, the same page the Utilities tab opens (Zach, 2026-10-29)
    needsId: () => false,
    waiting: () => false,
    newIdBox: () => '',
    // the quiet line under a busy utility: '' when it is not starting or running
    busy: (action) => {
      const now = phase(action);
      if (now === 'dry') return `Starting ${plainTitle(action.title)}…`;
      if (now === 'running') return `${gerund(action.title)}… ${Math.max(0, Math.round((Date.now() - active.startedAt) / 1000))} s`;
      return '';
    },
    panel: () => '',
  };
}

// The menu's rows: the active utility menu (u0's quiet rows by default: icon + title, the description as a tooltip,
// Checks / Maintenance / Motion) inside the header's dropdown, then "All utilities…", which opens the Utilities tab.
function renderUtilities(actions, path) {
  if (!actions || !actions.length) return '';
  const menu = menus()[S.menu] || menus()[DEFAULTS.menu];
  if (!menu) return '<div class="muted">No utility menu loaded (utilities/*.js).</div>';
  // WHY at the bottom (Zach, 2026-10-29): "At the bottom of the menu, an option to expand, which takes you to the main utility page"
  const all = '<div class="u-allrow"><button class="u-all" data-util-all role="menuitem">'
    + '<svg class="u-ic" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18M9 9v11"/></svg>'
    + '<span class="u-text"><span class="u-title">All utilities…</span></span></button></div>';
  try {
    return `${menu.html(utilityContext(actions, path)) || ''}${all}`;
  } catch (error) {
    console.error(`utility menu ${S.menu}:`, error);
    return `<div class="err">utility menu ${esc(S.menu)} failed: ${esc(error)}</div>`;
  }
}

// The entity header's tools: Utilities ▾ at the same place on every entity page (disabled where there are none) and its
// menu. A click in it runs the utility or opens its Run page (the modal), exactly as the Utilities tab's button does.
const CARET = '<svg class="ent-caret" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
function entityTools() {
  const detail = S.sel && !S.selKey && S.node && S.node.node.path === S.sel ? S.node : null;
  const actions = (detail && detail.actions) || [];
  const path = detail ? detail.node.path : null;
  const none = !actions.length;
  const open = S.menuOpen && !none;
  const title = S.selKey ? 'A grouping row has no utilities' : !detail ? 'Utilities (loading…)'
    : none ? 'No utilities for this entity' : `${actions.length} utilit${actions.length === 1 ? 'y' : 'ies'} · ${actions[0].owner || 'its plugin'} runs them`;
  const button = `<button class="ent-util-btn${open ? ' on' : ''}" id="ent-util-btn" aria-haspopup="menu" aria-expanded="${open}" aria-controls="ent-menu"${none ? ' disabled' : ''} title="${esc(title)}">`
    + `<span>Utilities</span>${CARET}</button>`;
  const menu = open ? `<div class="ent-menu" id="ent-menu" role="menu" aria-label="Utilities">${renderUtilities(actions, path)}</div>` : '';
  return `<div class="ent-tools">${button}${menu}</div>`;
}

// The quiet one-line run status under the entity header: "Updating firmware on moteus-c1 · CAN 21… 42 s" while it runs
// (muted, never a warning colour), then its result ("Firmware updated to 1.1.2 · just now · Details ▸"); Details opens
// the run's records and output tail. Only a failure is a diagnostic-level line, the one place colour is allowed.
function runLine() {
  const path = S.selKey ? null : S.sel;
  const util = S.util && S.util.path === path && ['dry', 'running', 'done'].includes(S.util.phase) ? S.util : null;
  if (!util) return '';
  if (util.phase === 'dry') {
    return `<div class="ent-run running" role="status" aria-live="polite"><span class="ent-run-t">Starting ${esc(plainTitle(util.title))} on ${esc(util.label)}…</span></div>`;
  }
  const run = util.runId && window.IORuns ? window.IORuns.get(util.runId) : null;
  const custom = run && mountedRunView && runViews()[mountedRunView].runLine ? runViews()[mountedRunView].runLine(util, run, runsCtx) : null;
  if (custom) return custom; // a run view that draws the line itself (r2: the output in place)
  const show = util.runId && canShowRun() ? `<button class="ent-run-more" data-run-open="${esc(util.runId)}">Show output</button>` : '';
  if (util.phase === 'running') {
    const seconds = Math.max(0, Math.round((Date.now() - util.startedAt) / 1000));
    return `<div class="ent-run running" role="status" aria-live="polite"><span class="ent-run-t">${esc(gerund(util.title))} on ${esc(util.label)}… ${seconds} s${show ? ' · ' : ''}</span>${show}</div>`;
  }
  const outcome = runOutcome(util);
  const age = (Date.now() - util.doneAt) / 1000;
  const when = age < 10 ? 'just now' : `${ago(age)} ago`;
  const words = outcome.ok ? outcome.text : `${plainTitle(util.title)} failed · ${outcome.text}`;
  const more = `<button class="ent-run-more" data-run-details aria-expanded="${S.runDetails}">Details ${S.runDetails ? '▾' : '▸'}</button>`;
  return `<div class="ent-run ${outcome.ok ? 'ok' : 'fail'}" role="status"${outcome.ok ? '' : ' data-diagnostic'}>`
    + `${outcome.ok ? '' : shape('error', 'failed')}<span class="ent-run-t" title="${esc(words)}">${esc(words)} · ${esc(when)} · </span>${show ? `${show}<span class="ent-run-t"> · </span>` : ''}${more}</div>`
    + (S.runDetails ? `<div class="ent-run-details">${runDetailsHtml(util)}</div>` : '');
}

// the run's events and output tail, as the host answered (Details ▸)
function runDetailsHtml(util) {
  if (!util.result) return `<div>${esc(util.refused)}${util.error ? ` · ${esc(util.error)}` : ''}</div>`;
  const result = util.result;
  const lines = (result.records || []).map((record) => `<li><span class="mono">${esc(record.code || record.record || '')}</span> ${esc(record.message || record.name || '')}</li>`).join('');
  return `${lines ? `<ul class="u-records">${lines}</ul>` : '<div class="muted">No records.</div>'}`
    + `${(result.rejected || []).length ? `<pre class="u-tail">${esc(result.rejected.join('\n'))}</pre>` : ''}`
    + `${result.stderr_tail ? `<pre class="u-tail">${esc(result.stderr_tail)}</pre>` : ''}`
    + `<div class="muted">${esc(result.plugin || '')} · ${esc(result.outcome)} · ${esc(result.duration_s)} s · exit ${esc(result.returncode)}`
    + `${(result.argv || []).length ? ` · <code>${esc(result.argv.join(' '))}</code>` : ''}</div>`;
}

// The Run page: the ONE page between a click and a run, the same for the menu and the Utilities tab. It says in plain words
// what happens (the plugin's own confirm sentence, from the dry run once it has answered), holds the inputs (set_id's new
// id), states what the person confirms by pressing Run (a moves utility's "nothing is in the way", the host's second
// confirmation), and has one Run button. WHY no tick box (Zach, 2026-10-29: "After you click a utility it can go to at
// most 1 other page, if you need to enter information or confirm the area is clear, and then it should just run"): the
// confirmations are the question the Run button answers, so a confirm utility runs on exactly one more click.
function renderModal() {
  const back = $('modal');
  const util = S.util;
  const action = util && util.phase === 'confirm' && S.node && util.path === S.node.node.path && (S.node.actions || []).find((item) => item.name === util.name);
  if (!action) {
    back.hidden = true;
    S.modalFocused = false;
    if (S.modalHtml) { back.innerHTML = ''; S.modalHtml = ''; }
    return;
  }
  back.hidden = false;
  const answer = (util.dry && util.dry.result && util.dry.result.action_result) || {};
  const batch = (util.dry && util.dry.result && util.dry.result.batch) || null;
  const label = (S.built && S.built.byEntity.has(util.path) && S.built.byKey.get(S.built.byEntity.get(util.path)).entity === util.path)
    ? S.built.byKey.get(S.built.byEntity.get(util.path)).label : S.node.node.name;
  const again = secondConfirmOf(util) || action.second_confirm || '';
  const confirmations = [...(action.confirmations || []), ...(again ? [again] : [])];
  const typed = action.name === 'set_id' ? newIdOf(util.path, action) : null;
  const ready = RUNNABLE.has(action.state) && !util.asking && (!typed || typed.id !== null);
  const input = typed ? `<label class="m-newid-in"><span>New CAN id (1-126)</span><input type="number" min="1" max="126" step="1" inputmode="numeric"`
    + ` data-util-newid value="${esc(S.newIds.get(util.path) ?? '')}" aria-label="New CAN id (1-126)" autofocus></label>`
    + `<div class="m-newid">${typed.id !== null ? `CAN ${esc(util.node)} → CAN ${esc(typed.id)}` : `<span class="muted">${esc(typed.problem)}</span>`}</div>` : '';
  const failed = !util.asking && !util.dry && util.refused ? `<div class="m-refused">${shape('error', 'not run')}<span><b>${esc(util.refused)}</b>${util.error ? ` · ${esc(util.error)}` : ''}</span></div>` : '';
  // a batch (all:<action> of a composite) shows its N runs in the open: what one Run starts is the point of its page
  const runs = batch ? `<div class="m-runs">${batchRunsHtml(batch)}</div>` : '';
  const ladder = util.dry && !batch ? ladderHtml(answer) : '';
  const how = util.dry && !batch && !ladder ? `<code class="u-cmd">${esc(commandOf(answer) || 'in-process (no command line)')}</code>${stepsHtml(answer)}` : '';
  // WHY the dry run's danger may raise the action's: Reset… is `interrupts` until its plan runs a moteus restart, which drops a
  // loaded joint (`moves`, docs/device-reset.md); the page then gives it the moves treatment, never a lower one
  const danger = answer.danger === 'moves' ? 'moves' : action.danger;
  const html = `<div class="modal m-${esc(danger)}" role="dialog" aria-modal="true" aria-labelledby="m-title" data-run-page="${esc(action.name)}">`
    + `<header>${utilIcon(action.name)}<h2 id="m-title">${esc(plainTitle(action.title))} · ${esc(label)}</h2><span class="danger ${esc(danger)}">${esc(danger)}</span></header>`
    + `<div class="body"><div class="confirm" data-run-says>${esc(answer.confirm || action.confirm)}</div>${input}${runs}${ladder}`
    + (confirmations.length ? `<div class="m-pre-h">Pressing Run confirms</div><ul class="m-confirms">${confirmations.map((text) => `<li>${esc(text)}</li>`).join('')}</ul>` : '')
    + failed
    + (how ? `<button class="u-how-t" data-sheet-more aria-expanded="${S.sheetMore}">How it runs ${S.sheetMore ? '▾' : '▸'}</button><div class="u-how${S.sheetMore ? ' open' : ''}">${how}</div>` : '')
    + `</div><footer><span class="note">${util.asking ? `Asking ${esc(action.owner)} for its token…` : `${esc(action.owner)} runs it when you press Run.`}</span>`
    + `<button class="btn" data-util-cancel>Cancel</button>`
    + `<button class="btn primary ${esc(danger)}" data-util-confirm${ready ? '' : ' disabled'}>Run</button></footer></div>`;
  if (html !== S.modalHtml) {
    const typing = document.activeElement && document.activeElement.matches && document.activeElement.matches('#modal [data-util-newid]');
    back.innerHTML = html;
    S.modalHtml = html;
    const box = back.querySelector('[data-util-newid]');
    if (box && (typing || !S.modalFocused)) { const value = box.value; box.focus(); box.value = ''; box.value = value; }
    S.modalFocused = true;
  }
}

// ------------------------------------------------------------------------ the expected setup ----
// The selected row's place in the person's rig file (docs/expected-setup.md) and the three edits: Expect what's here
// (a device or a rig row), Forget (an absent row), Retire… (a device, with a reason). WHY a plain confirm and no dry
// run: they rewrite the person's own file and never touch a device (Zach, 2026-10-23); the confirm shows the host's
// own sentence for the exact edit (/api/v1/node setup.confirm), the same plan the POST runs.
const SETUP_TITLES = { expect: "Expect what's here", forget: 'Forget', retire: 'Retire…' };
const SETUP_VERBS = { expect: 'Expect', forget: 'Forget', retire: 'Retire' };
const SETUP_OPS = { device: ['expect', 'retire'], rig: ['expect'], absent: ['forget', 'retire'] };  // disabled unless setup.can[op]

function setupWhere(setup) {
  const nodes = (list) => (list.length ? list.join(', ') : 'no nodes');
  if (setup.role === 'rig') return `<div class="s-line">Rig <b>${esc(setup.rig)}</b> · one row that counts what it expects</div>`;
  const lines = [];
  if (setup.retired) lines.push(`<div class="s-line s-retired">Retired ${esc(setup.retired.date)}: ${esc(setup.retired.reason)}</div>`);
  const notes = setup.node_notes || {};
  const expected = setup.nodes_expected.map((node) => (notes[node] ? `${node} (${notes[node]})` : node));
  if (setup.expected) lines.push(`<div class="s-line">In <b>${esc(setup.rig)}</b> · expects ${esc(nodes(expected))}</div>`);
  else if (!setup.retired) lines.push(`<div class="s-line muted">${setup.role === 'absent' ? 'Remembered by this host or listed by its driver, not in the rig file' : 'Not in the expected setup'}</div>`);
  if (setup.role === 'device') lines.push(`<div class="s-line muted">Answering now: ${esc(nodes(setup.nodes_here))}</div>`);
  return lines.join('');
}

function setupPanel(setup, edit) {
  const confirm = String(setup.confirm[edit.op] || '').replace('<reason>', edit.reason.trim() || '<reason>');
  const reason = edit.op === 'retire'
    ? `<label class="s-reason"><span>Reason</span><input type="text" data-setup-reason value="${esc(edit.reason)}" placeholder="why it is retired" aria-label="Why it is retired"></label>` : '';
  const rig = edit.op === 'expect' && setup.needs_rig
    ? `<label class="s-reason"><span>Rig</span><select data-setup-rig>${setup.rigs.map((name) => `<option${name === edit.rig ? ' selected' : ''}>${esc(name)}</option>`).join('')}</select></label>` : '';
  const ready = edit.op !== 'retire' || edit.reason.trim();
  if (edit.phase === 'done') {
    const result = edit.result || {};
    return `<div class="s-panel"><div class="u-result ${result.ok ? 'ok' : 'fail'}"><div class="u-result-h">${result.ok ? '' : shape('error', 'not written')}`
      + `<b>${result.ok ? 'Saved' : `Not saved: ${esc(result.refused || 'error')}`}</b> · ${esc(result.message || result.error || '')}</div></div>`
      + `<div class="u-foot"><button class="btn" data-setup-close>Close</button></div></div>`;
  }
  return `<div class="s-panel"><div class="confirm">${esc(confirm)}</div>${reason}${rig}`
    + `<div class="s-note muted">Writes ${esc(setup.file || 'the rig file')} · nothing is sent to a device</div>`
    + `<div class="u-foot"><button class="btn" data-setup-cancel>Cancel</button>`
    + `<button class="btn primary query" data-setup-confirm${ready && edit.phase !== 'saving' ? '' : ' disabled'}>${esc(SETUP_VERBS[edit.op])}</button></div></div>`;
}

function setupBody(detail) {
  const setup = detail && detail.setup;
  if (!setup) return '';
  const path = detail.node.path;
  const edit = S.setup && S.setup.path === path ? S.setup : null;
  const buttons = (SETUP_OPS[setup.role] || []).map((op) => `<button class="btn${edit && edit.op === op ? ' on' : ''}" data-setup="${op}"${setup.can[op] ? '' : ' disabled'} title="${esc(setup.confirm[op] || '')}">${esc(SETUP_TITLES[op])}</button>`).join('');
  const note = !setup.editable ? `<div class="s-line muted">${esc(setup.note || 'No rig file on this host (a replay without --expected): nothing to edit')}</div>` : '';
  return `${setupWhere(setup)}${note}<div class="s-actions">${buttons}</div>${edit ? setupPanel(setup, edit) : ''}`;
}

async function saveSetup() {
  const edit = S.setup;
  if (!edit || edit.phase !== 'confirm' || (edit.op === 'retire' && !edit.reason.trim())) return;
  edit.phase = 'saving';
  render();
  let result;
  try {
    const response = await fetch(`/api/v1/${edit.op}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' },
      body: JSON.stringify({ path: edit.path, rig: edit.rig || null, reason: edit.reason.trim() }) });
    result = await response.json();
  } catch (error) {
    result = { ok: false, refused: 'Unreachable', message: `the host did not answer: ${error}` };
  }
  if (S.setup !== edit) return;
  edit.phase = 'done';
  edit.result = result;
  if (result.ok && edit.op === 'forget') {
    // a forgotten row is gone: show the result on its rig's row (else the host), which is what changed
    const rigs = (S.data && S.data.expected_setup && S.data.expected_setup.rigs) || [];
    const target = (rigs.find((rig) => rig.name === edit.rig) || {}).path || S.data.tree.path;
    select(target);
    pushNav();
    S.setup = { ...edit, path: target };
    render();
    return;
  }
  S.nodeAt = 0; // the row's card and the rig row change at once
  loadNode();
  render();
}

// ------------------------------------------------------------------- what to check (plain words) ----
// A status whose cause has a checklist carries the value `what_to_check` (numbered lines, newline-separated, in the
// order to check them; moteus_servo docs/io_diagnostics_actions.md, the host's ExpectedAbsent rows). The detail panel
// shows the plain-words reason and those lines, as authored.
// WHY not a list over the panes as well: Zach (2026-10-23) "Please get rid of this activity stream here at the top...
// If I want to see this then I could go to the activity screen." A problem is its row's mark, the tray chip, the
// entity's "What to check" and the Activity screen; the page's first selection is the worst cause, so its checklist
// is still the first thing on screen.
function whyOf(detail) {
  return ((detail && detail.checks) || []).filter((check) => !check.not_run && PROBLEM.has(check.effective_level)).map((check) => {
    const values = Object.fromEntries((check.values || []).map((value) => [value.key, value.value]));
    if (!values.what_to_check) return null;
    return { name: check.name, level: check.effective_level, message: check.message || '', code: check.code || '',
      lines: String(values.what_to_check).split('\n').filter((line) => line.trim()), evidence: values.kernel_evidence || '' };
  }).filter(Boolean);
}

// /api/v1/node for a path whose checklist the page shows (the selection's problem parts); cached,
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

// The detail panel's "What to check": the selection's own checklists, then its folded parts' (the CAN bus folded
// into its adapter), each with the plain-words reason.
function whatToCheckBody(detail, ownRow) {
  const own = whyOf(detail).map((why) => ({ ...why, who: '' }));
  const parts = ((ownRow && ownRow.parts) || []).filter((part) => part.problem).flatMap((part) => {
    wantWhy(part.entity, part.message);
    return ((S.why.get(part.entity) || {}).checks || []).map((why) => ({ ...why, who: part.label, entity: part.entity }));
  });
  const all = [...own, ...parts];
  if (!all.length) return '';
  return all.map((why) => `<div class="why-item">`
    + `<div class="why-head">${shape(why.level, why.level)}<b>${why.entity ? `<a class="link" data-go="${esc(why.entity)}">${esc(why.who)}</a> · ` : ''}${esc(why.name)}</b></div>`
    + `<div class="why-msg">${esc(usb(why.message))}</div>${whyLines(why)}</div>`).join('');
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
    + S.data.summary.attention.map((a) => `<div class="pop-row" data-go="${esc(a.path)}"><span class="pop-st">${shape(a.level)}</span><span class="x"><b>${esc(a.device && a.device.path !== a.path ? `${usb(a.device.name)} › ${usb(a.name)}` : usb(a.name))}</b> — ${esc(usb(a.message))}</span></div>`).join('')
    + '<h4 style="margin-top:10px">Recent</h4>'
    + S.data.recent.slice(0, 5).map((e) => `<div class="pop-row" data-tab="activity"><span class="t">${esc(e.time.slice(0, 5))}</span><span class="x">${esc(usb(e.message))}</span></div>`).join('')
    + '<button class="pop-open" data-close="1">Open RobotDx</button>'
    + '<div class="pop-foot">The tray renders this same model (/api/v1/state → chip) through V2\'s AppIndicator.</div>';
}

const HOST_LOST_S = 30;
function renderStatusbar() {
  const since = (performance.now() - S.lastOk) / 1000;
  // WHY calm first (Zach, 2026-11-04: "A server restart is invisible"): sps restarts Preview on every .py that lands, a matter of seconds; the page keeps
  // its data, and only a host still gone after HOST_LOST_S is a problem worth a colour
  const lost = S.lastError && since >= HOST_LOST_S;
  const message = !S.lastError ? `Last update ${since < 1.5 ? '0' : since.toFixed(0)} s ago`
    : lost ? `Host unreachable · last update ${ago(since)} ago` : `Reconnecting · ${since.toFixed(0)} s ago`; // WHY short: this bar ellipsizes, and the age is the point
  $('lastmsg').textContent = message;
  $('lastmsg').className = lost ? 'late' : '';
  const renderer = renderers()[S.variant];
  if (renderer) $('variant-switch').title = `Tree proposal ${S.variant} · ${renderer.name}${renderer.description ? ` — ${renderer.description}` : ''}`;
}

// --------------------------------------------------------------------------------- render ----
function renderNow() {
  if (!S.data) return;
  materializeTree();
  const byPath = index(S.data.tree);
  S.byPath = byPath;
  S.built = IOView.build(S.data, S.structure, { ...structureOptions(), query: S.query, sort: S.sort, changes: changesOf(S.act), into: S.into, inside: S.inside, connected: S.connected });
  // the row stepped into is gone (unplugged, or this grouping does not have it): back to the whole tree, quietly
  if (S.into && S.built.intoMissing) { S.into = null; replaceNav(); S.built = IOView.build(S.data, S.structure, { ...structureOptions(), query: S.query, sort: S.sort, changes: changesOf(S.act), inside: S.inside, connected: S.connected }); }
  renderTreeNav();
  renderClock();
  const fixture = S.data.fixture;
  $('mode').textContent = fixture ? `Replay ${fixture.name} · ${fixture.label} (recorded)` : `Live · ${S.data.host.name}`;
  const skipped = S.data.host.sources.filter((s) => s.state === 'skipped');
  $('sources').hidden = !skipped.length;
  $('sources').innerHTML = skipped.map((s) => `<span class="tag simulated" title="${esc(s.note)}">${esc(s.name)} skipped</span>`).join('');
  document.querySelectorAll('#views button').forEach((b) => b.classList.toggle('on', b.dataset.view === S.view));
  const treeView = S.view === 'tree';
  const tableView = S.view === 'table';
  const threedView = S.view === 'threed'; // the tree stays drawn beside it, as in the Tree view
  const layersView = Boolean(window.IOLayers) && window.IOLayers.VIEWS.includes(S.view); // Deploy, Teleop, Data: the same
  $('main').className = `main v-${S.view}${treeView && WIDE.has(S.variant) ? ' wide-tree' : ''}${tableView && inspectorShown() ? ' insp-open' : ''}`;
  $('insp-toggle').hidden = !tableView;
  $('insp-toggle').setAttribute('aria-pressed', String(S.inspPinned));
  $('insp-toggle').classList.toggle('on', S.inspPinned);
  $('inspector').setAttribute('aria-hidden', String(tableView && !inspectorShown()));
  $('insp-pin').setAttribute('aria-pressed', String(S.inspPinned));
  $('insp-pin').classList.toggle('on', S.inspPinned);
  renderViewOptionsDot();
  const legacy = treeView && isLegacy(S.variant);
  $('structure-switch').classList.toggle('dim', legacy);
  $('structure-switch').title = legacy ? `${S.variant} is a legacy style: it always draws the physical API tree` : 'Structure: what the rows are';
  const activityView = S.view === 'activity';
  // WHY keepTree: a layout (layouts/*.js) that holds the tree in a sidebar keeps it drawn beside the Table and Activity
  const keepTree = Boolean(window.IOLayoutHost && window.IOLayoutHost.keepsTree());
  $('tree-host').hidden = !treeView && !threedView && !layersView && !keepTree;
  $('left-other').hidden = treeView || activityView || threedView || layersView;
  $('activity-host').hidden = !activityView;
  $('threed-host').hidden = !threedView;
  $('layers-host').hidden = !layersView;
  if (!activityView && window.IOActivityLog) window.IOActivityLog.setVisible(false);
  if (treeView || threedView || layersView || keepTree) renderTreePane();
  if (activityView) renderActivityView();
  else if (!treeView && !threedView && !layersView) $('left-other').innerHTML = renderTable();
  const noSearch = (treeView || threedView || layersView || keepTree) && NO_QUERY.has(S.variant);
  $('search').disabled = noSearch;
  $('search').placeholder = noSearch ? `No search in ${S.variant} · try Table` : 'Search entities, ids, ports…';
  $('search').closest('.search').classList.toggle('off', noSearch);
  // WHY drawn through a layout's ctx.draw: an unchanged detail is not rewritten every second (selection, focus, scroll)
  renderDetail(byPath);
  renderChip();
  renderModal();
  renderStatusbar();
  updateRunView();
  if (window.IOThreeD) window.IOThreeD.update(threedView);
  if (window.IOLayers) window.IOLayers.update(S.view);
  if (window.IOLayoutHost) window.IOLayoutHost.update();
}

// A new selection: its node loads, the drill below the old one closes. A utility belongs to the entity it was opened
// on: a dry run closes, a run (running or done) stays with its entity and comes back with it; so does a setup edit.
// The caller pushes the history entry (pushNav), or replaces it when the page chose the selection itself.
function select(path) {
  S.sel = path;
  S.selKey = null;
  S.node = null;
  S.drill = { ...NO_DRILL };
  S.menuOpen = false;
  stashRun(path);
  if (S.setup && S.setup.path !== path) S.setup = null;
  loadNode();
}

// ------------------------------------------------------------------------------- events ----
document.addEventListener('click', (event) => {
  const t = event.target;
  // View options: its button toggles it, a click anywhere else closes it (and still does what it clicked)
  if (t.closest('[data-load-retry]')) { loadNode(true); return; }
  if (t.closest('#vo-btn')) { setViewOptions(!S.vo); return; }
  if (S.vo && !t.closest('#view-options')) setViewOptions(false);
  // the entity header's Utilities ▾: its button toggles the menu, a click anywhere else closes it (light dismiss)
  if (t.closest('#ent-util-btn')) { S.menuOpen = !S.menuOpen; return render(); }
  if (t.closest('[data-util-menu]')) { S.menuOpen = true; event.stopPropagation(); return render(); }
  if (S.menuOpen && !t.closest('.ent-menu')) { S.menuOpen = false; render(); }
  if (t.closest('[data-run-details]')) { S.runDetails = !S.runDetails; return render(); }
  if (t.closest('[data-sheet-more]')) { S.sheetMore = !S.sheetMore; return render(); }
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
  // the popover's and the Prototype picker's selects and switches report through change events
  if (t.closest('#controls') || t.closest('#view-options')) return;
  if (t.closest('#insp-toggle')) return setPinned(!S.inspPinned);
  if (t.closest('#insp-pin')) return setPinned(!S.inspPinned);
  if (t.closest('#insp-close')) { S.inspPinned = false; writePref(PREFS.inspectorPinned, '0'); return setInspector(false); }
  const sec = t.closest('[data-sec-toggle]');
  if (sec) return toggleSection(sec.dataset.secToggle, sec.getAttribute('aria-expanded') === 'true');
  const view = t.closest('[data-view]');
  if (view) { S.view = view.dataset.view; pushNav(); return render(); }
  if (t.closest('#chip')) { S.chip = !S.chip; return render(); }
  const go = t.closest('[data-go]');
  if (go) {
    // the selection itself again (a "back to this entity" link): only its drill closes, the node stays loaded
    if (go.dataset.go === S.sel && !S.selKey) { S.drill = { ...NO_DRILL }; S.menuOpen = false; } else select(go.dataset.go);
    S.chip = false;
    if (S.view === 'activity') S.view = 'tree';
    pushNav();
    return render();
  }
  if (t.closest('[data-tab]')) { S.view = t.closest('[data-tab]').dataset.tab; S.chip = false; pushNav(); return render(); }
  if (t.closest('[data-close]')) { S.chip = false; return render(); }
  // utilities, from the menu or the Utilities tab: an immediate one runs on the click, any other opens its Run page
  if (t.closest('[data-util-all]')) { S.menuOpen = false; openPage('utilities'); return undefined; }
  const util = t.closest('[data-util]');
  if (util) { if (!util.disabled) startUtility(util.dataset.util); return undefined; }
  if (t.closest('[data-util-confirm]')) { confirmRun(); return undefined; }
  if (t.closest('[data-util-cancel]') || t.id === 'modal') { if (S.util && S.util.phase === 'confirm') S.util = null; return render(); }
  const showRun = t.closest('[data-run-open]');
  if (showRun) { openRun(showRun.dataset.runOpen); return undefined; }
  // the Expected setup card: a click opens its confirm; only the confirm's button writes the file
  const setupButton = t.closest('[data-setup]');
  if (setupButton && S.node && S.node.setup) {
    const setup = S.node.setup;
    // rig: the one to add to when the host asks which (needs_rig); for Forget, the rig it leaves (its result shows there)
    S.setup = { path: S.node.node.path, op: setupButton.dataset.setup, phase: 'confirm', reason: '',
      rig: setup.needs_rig ? setup.rigs[0] : (setupButton.dataset.setup === 'forget' ? setup.rig : null), result: null };
    return render();
  }
  if (t.closest('[data-setup-confirm]')) { saveSetup(); return undefined; }
  if (t.closest('[data-setup-cancel]') || t.closest('[data-setup-close]')) { S.setup = null; return render(); }
  if (t.closest('[data-setup-reason]') || t.closest('[data-setup-rig]')) return undefined;
  const row = t.closest('[data-key]');
  if (row && S.view === 'table') {
    // the selected row again folds the inspector (unless pinned); any other row slides it in on that row
    if (row.dataset.key === selectedKey() && inspectorShown() && !S.inspPinned) return setInspector(false);
    S.inspOpen = true;
    onTreeSelect(row.dataset.key);
    return render();
  }
  if (row) { if (S.view === 'activity') S.view = 'tree'; onTreeSelect(row.dataset.key); pushNav(); return render(); }
  // a click back into the table's empty space folds the inspector
  if (S.view === 'table' && t.closest('#left') && S.inspOpen && !S.inspPinned) return setInspector(false);
  if (S.chip && !t.closest('#chip-pop')) { S.chip = false; render(); }
});
$('search').addEventListener('input', (event) => { S.query = event.target.value; render(); });
$('depth-colormap').addEventListener('change', (event) => setDepth({ depth_colormap: event.target.value }));
$('depth-equalize').addEventListener('change', (event) => setDepth({ depth_equalize: event.target.checked }));
for (const id of ['depth-min', 'depth-max']) {
  $(id).addEventListener('change', () => {
    const low = Number($('depth-min').value), high = Number($('depth-max').value);
    if ($('depth-min').value === '' || $('depth-max').value === '') return renderDepthControls();
    setDepth({ depth_min_m: low, depth_max_m: high });
  });
}
$('history-window').addEventListener('change', (event) => { const seconds = Number(event.target.value); if (seconds > 0) setWindow(seconds); });
// the scroll position of the drill step on screen (restored when back / forward returns to it); a scroll while another
// step is still loading belongs to no step
$('detail').addEventListener('scroll', () => { if (S.drawnAt && S.drawnAt === drillAt()) S.scrolls.set(S.drawnAt, $('detail').scrollTop); }, { passive: true });
$('structure-toggles').addEventListener('change', (event) => {
  if (event.target.closest('[data-connected]')) { setConnected(event.target.checked); return; }
  const box = event.target.closest('[data-opt]');
  if (box) setToggle(box.dataset.opt, box.checked);
});
document.addEventListener('change', (event) => {
  if (event.target.closest('[data-setup-rig]') && S.setup) { S.setup.rig = event.target.value; render(); }
});
document.addEventListener('input', (event) => {
  if (event.target.closest('[data-setup-reason]') && S.setup) { S.setup.reason = event.target.value; render(); return; }
  const box = event.target.closest('[data-util-newid]');
  if (!box || !S.util || S.util.name !== 'set_id' || S.util.phase !== 'confirm') return;
  S.newIds.set(S.util.path, box.value); // the Run page checks it as it is typed (newIdOf); its Run button asks for the token
  render();
});
document.addEventListener('keydown', (event) => {
  // WHY: a terminal program (vi, less, a prompt) needs Escape and every other key; the page's layers stay out of it
  if (event.target.closest && event.target.closest('.xterm')) return;
  const typing = event.target.closest && event.target.closest('input, select, textarea, [contenteditable]');
  // `]` pins the Table's inspector open (again: unpins and folds it), as VS Code toggles its secondary side bar
  if (event.key === ']' && !typing && !event.ctrlKey && !event.metaKey && !event.altKey && S.view === 'table') {
    setPinned(!S.inspPinned);
    event.preventDefault();
    return;
  }
  if (event.key === 'Enter' && event.target.closest && event.target.closest('#modal [data-util-newid]')) { confirmRun(); event.preventDefault(); return; }
  if (event.key !== 'Escape') return;
  // one layer at a time, innermost first: the Run page, the Utilities menu, the setup edit, the chip, View options,
  // then the inspector
  if (S.util && S.util.phase === 'confirm') S.util = null;
  else if (S.menuOpen) { S.menuOpen = false; render(); $('ent-util-btn') && $('ent-util-btn').focus(); event.preventDefault(); return; }
  else if (S.setup && S.setup.phase === 'confirm') S.setup = null;
  else if (S.chip) S.chip = false;
  else if (S.vo) setViewOptions(false);
  else if (S.view === 'table' && S.inspOpen && !S.inspPinned) S.inspOpen = false;
  render();
});

document.addEventListener('keydown', (event) => {
  if (!(event.ctrlKey || event.metaKey) || !event.shiftKey || event.altKey || event.key !== 'Backspace') return;
  if (event.target && event.target.closest && event.target.closest('input, textarea, select, [contenteditable]')) return;
  if (!S.into) return;
  event.preventDefault();
  stepUp();
});

async function boot() {
  // index.html's inline script already set data-skin / data-mode from storage, so the first paint is right
  const [skins, structures, detailLayouts, utilityMenus, runEntries, chartEntries] = await Promise.all([readManifest('skins/index.json', 'skins'),
    readManifest('view/structure/index.json', 'structures'), readManifest('detail/index.json', 'layouts'), readManifest('utilities/index.json', 'menus'),
    readManifest('runs/index.json', 'runs'), readManifest('charts/index.json', 'charts')]);
  S.skins = skins.entries.length ? skins.entries : [{ id: DEFAULTS.skin, name: 'shadcn/ui', file: 'shadcn.css', primary: true }];
  await Promise.all([
    ...S.skins.map((skin) => loadStylesheet(`skins/${skin.file}`, skin.id)),
    ...structures.entries.map((entry) => loadScript(`view/structure/${entry.file}`)),
    ...detailLayouts.entries.map((entry) => loadScript(`detail/${entry.file}`)),
    ...utilityMenus.entries.map((entry) => loadScript(`utilities/${entry.file}`)),
    ...runEntries.entries.map((entry) => loadScript(`runs/${entry.file}`)),
    ...chartEntries.entries.map((entry) => loadScript(`charts/${entry.file}`)),
  ]);
  $('tree-host').prepend(treeNav());
  setupSkins(skins.default);
  setupStructures(structures);
  setupSwitcher();
  setupPrototype(detailLayouts.default, utilityMenus.default, runEntries.default, chartEntries.default);
  S.inspPinned = readPref(PREFS.inspectorPinned) === '1';
  S.connected = readPref(PREFS.connected) === '1';
  if (S.connected) presenceBeat();
  if (S.structure) renderToggles();
  // the page layout (layouts/host.js): moves the page's parts into one of five arrangements; everything it draws is ours
  if (window.IOLayoutHost) {
    await window.IOLayoutHost.start({ S, $, esc, usb, render, entityLabel, renderActivity: typeof renderActivity === 'function' ? renderActivity : null, eventRow, setViewOptions, setToggle, structureOptions, setConnected,
      toggleShown: (toggle) => IOView.toggleShown(toggle, structureOptions()), startDryRun, select: onSelectEntity, stepInto, stepUp, addInside, canEnter,
      setView(view) { S.view = view; pushNav(); render(); } });
  }
  if (S.view === 'table' && (S.sel || S.selKey) && (S.drill.diag || S.drill.page)) S.inspOpen = true;
  // the 3D view (threed/core.js): the page's selection, history, history cache and drawing helpers; it never reads the API's tree itself
  if (window.IOThreeD) {
    window.IOThreeD.attach({ S, esc, usb, icon, shape, pill, entityLabel, render, replaceNav, cached,
      select: onSelectEntity,
      // Open: the entity's own device page, one history step after the 3D view (Back returns to it, selection and camera kept)
      open(path) {
        if (path !== S.sel || S.selKey) { select(path); pushNav(); }
        S.view = 'tree';
        S.drill = { ...NO_DRILL };
        pushNav();
        render();
      } });
  }
  // the placeholder workspaces (layers/layers.js): the page's selection, URLs and drawing helpers; every link on them is a page that exists
  if (window.IOLayers) {
    window.IOLayers.attach({ S, esc, entityLabel,
      // a link's page, as the URL that would open it (the selection kept), and going there: one history step after the layer's page
      url: (nav) => navUrl({ ...navNow(), ...nav }),
      go({ view, page }) {
        S.view = view || 'tree';
        S.drill = { ...NO_DRILL, page: page || null };
        S.menuOpen = false;
        pushNav();
        render();
      } });
  }
  setupPanes();
  loadSettings();
  tick();
  setInterval(tick, 1000);
}
boot();
