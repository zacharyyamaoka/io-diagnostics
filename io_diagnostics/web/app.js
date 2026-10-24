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
const S = {
  view: ['tree', 'table', 'activity'].includes(params.get('view')) ? params.get('view') : 'tree',
  sel: params.get('group') ? null : params.get('sel'),
  query: '',
  data: null,          // /api/v1/state
  act: null,           // /api/v1/activity
  actAt: 0,
  node: null,          // /api/v1/node for S.sel
  nodeAt: 0,
  open: new Map(),     // row key -> true/false, for the table and the built-in fallback tree
  selKey: params.get('group'), // the selected row when it stands for no entity (a structure's own grouping row)
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
  pickable: [],        // the structure ids View options' Group by lists (manifest `group`, in `pick` order)
  vo: false,           // the View options popover is open
  inspOpen: false,     // the Table's inspector, opened by a row click (folds away again)
  inspPinned: false,   // ... or pinned open (the toolbar toggle, `]`)
  layout: null,        // the detail layout drawing the detail panel (detail/*.js)
  menu: null,          // the utility menu drawing the Utilities section (utilities/*.js)
  runView: null,       // the run view drawing runs (runs/*.js): r1 by default
  closed: new Set(),   // detail section ids the viewer folded (remembered per section)
  actFilter: 'all',    // the activity log's filter: all | faults
  opened: new Set(),   // detail section ids the viewer opened that start folded (ctx.section(..., {defaultOpen: false}))
  layoutChoice: null,  // the Prototype picker's explicit detail layout (remembered), or null: the per-entity default
  menuOpen: false,     // the entity header's Utilities ▾ dropdown
  runs: new Map(),     // entity path -> its utility run (running / done) while the viewer looks at another entity
  ran: [],             // the utility runs this page made, newest first: drawn at the top of the Activity log
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
  sections: 'io-diagnostics.detail-sections',
  sectionsOpen: 'io-diagnostics.detail-sections-open',
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
// WHY shadcn: Zach, 2026-10-23: "I have outgrown the bare metal, it's honestly hurting my eyes now. Lets please just use
// stock shadcn for now ... what matters is the location of things"; every other skin stays one pick away (bottom-right)
// WHY d4 for the Computer (Zach, 2026-10-23, via the orchestrator): its page is the ROS2 System Webview's data (RAM, CPU,
// swap, load, disks, temperatures, GPU, network, USB); every other entity keeps d0 until he picks one
const DEFAULTS = { skin: 'shadcn', structure: 'p1', variant: 'v6', sort: 'status', layout: 'd0', computerLayout: 'd4', menu: 'u0', runs: 'r1' };
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
    if (window.IORuns && S.data.runs) { window.IORuns.ingest(S.data.runs); syncRunUtilities(); }
    S.lastOk = performance.now();
    S.lastError = '';
    if (!S.sel && !S.selKey) {
      S.sel = (S.data.summary.attention[0] || {}).path || S.data.tree.path;
      replaceNav(); // the first selection is the page's, not a step the viewer took: Back leaves the page
    }
    if (!history.state) replaceNav(); // a URL that came with ?sel=… starts the history at that exact state
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
    .catch(() => { if (S.sel === path) { S.sel = S.data.tree.path; S.node = null; S.drill = { ...NO_DRILL }; replaceNav(); } });
}

// ----------------------------------------------------------------------------------- back / forward ----
// Every drill-down is a page of the browser's history (Zach, 2026-10-23: "As I drill down please support
// forwards/backwards buttons on the mouse"): an entity (or grouping row) selection, a diagnostic or measurement page
// below it, a layout's own sub-page and the Tree / Table / Activity switch each push a URL that restores them
// (?sel=<path>&diag=<status name>&view=tree), and popstate puts that exact state back. The mouse's back / forward
// buttons and Alt+← / → are then the browser's own, in a tab and in the Chrome --app window.
// WHY in the shell, once: d1-d5 each kept a private drill stack the browser could not see; they read ctx.diagnostic.
const NAV_KEYS = ['sel', 'group', 'diag', 'dpath', 'm', 'page', 'view'];
const NO_DRILL = { diag: null, dpath: null, m: null, page: null };
const navNow = () => ({ sel: S.selKey ? null : S.sel || null, group: S.selKey || null, ...S.drill, view: S.view });
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
  S.view = ['tree', 'table', 'activity'].includes(nav.view) ? nav.view : 'tree';
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
    fetch(url, { cache: 'no-store' })
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

// ctx.redraw(): the detail again, now (a layout's own click, a hover scrub). Landed fetches coalesce into one.
function redrawDetail() { if (S.data && S.byPath) renderDetail(S.byPath); }
let redrawQueued = false;
function queueRedraw() {
  if (redrawQueued) return;
  redrawQueued = true;
  setTimeout(() => { redrawQueued = false; redrawDetail(); }, 0);
}

// The app-wide history window (GET / POST /api/v1/settings; View options). POST is page-only, as /api/v1/act.
const WINDOW_CHOICES = [60, 300, 600, 900, 1800, 3600, 10800, 21600, 43200, 86400];
const windowWords = (s) => (s < 3600 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);
async function loadSettings() {
  try { S.settings = await getJson('/api/v1/settings'); } catch (error) { S.settings = null; }
  renderWindowPicker();
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
    + `<span class="vo-label">${esc(toggle.label)}</span><input type="checkbox" role="switch" data-opt="${esc(toggle.key)}"${options[toggle.key] ? ' checked' : ''}></label>`).join('');
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
const registryIds = (registry, prefix) => Object.keys(registry).filter((id) => registry[id] && typeof registry[id][prefix] === 'function')
  .sort((a, b) => ((registry[a].order ?? 50) - (registry[b].order ?? 50)) || a.localeCompare(b));
function setupPrototype(layoutDefault, menuDefault, runsDefault) {
  const wire = (select, registry, method, pref, fallback, key) => {
    const ids = registryIds(registry, method);
    const stored = readPref(pref);
    S[key] = ids.includes(stored) ? stored : ids.includes(fallback) ? fallback : ids[0] || null;
    // WHY the run view shows only its id ("r1"): the bar's room is limited and the skin switcher beside it must stay in reach;
    // its name and description are the option's tooltip
    const label = (id) => (key === 'runView' ? esc(id) : `${esc(id)} · ${esc(registry[id].name || id)}`);
    const tip = (id) => (key === 'runView' ? `${registry[id].name || id}: ${registry[id].description || ''}` : registry[id].description || '');
    select.innerHTML = ids.map((id) => `<option value="${esc(id)}" title="${esc(tip(id))}">${label(id)}</option>`).join('');
    if (key === 'runView') select.title = 'Runs view: how a running utility shows its terminal (r1 · Terminal panel with tabs ... r5)';
    select.value = S[key] || '';
    select.addEventListener('change', () => {
      if (!registry[select.value]) return;
      S[key] = select.value;
      writePref(pref, select.value);
      if (key === 'layout') S.layoutChoice = select.value; // an explicit pick wins on every entity, the Computer too
      if (key === 'runView') mountRunView();
      render();
    });
    return ids.length;
  };
  const shown = wire($('detail-layout'), layouts(), 'render', PREFS.layout, layoutDefault || DEFAULTS.layout, 'layout')
    + wire($('utility-menu'), menus(), 'html', PREFS.menu, menuDefault || DEFAULTS.menu, 'menu')
    + wire($('run-view'), runViews(), 'mount', PREFS.runView, runsDefault || DEFAULTS.runs, 'runView');
  // no stored layout: the per-entity default (layoutFor) decides, and the picker shows what it chose
  S.layoutChoice = registryIds(layouts(), 'render').includes(readPref(PREFS.layout)) ? readPref(PREFS.layout) : null;
  $('proto-switch').hidden = shown < 4; // one layout, one menu and one run view: nothing to switch
  mountRunView();
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
// WHY the tag is neutral unless the event is a diagnostic: warn / error colours mean a true diagnostic result and
// nothing else (Zach, 2026-10-23), so "plugged", "unplugged", "boot" read as plain words; an event the host logged
// at warn / error (a fault confirmed, a bus going error-passive) takes its severity's tint.
function eventRow(event) {
  const diagnostic = PROBLEM.has(event.severity);
  return `<div class="ev${diagnostic ? ` sev-${esc(event.severity)}` : ''}" data-go="${esc(event.path)}"${diagnostic ? ' data-diagnostic' : ''}><span class="t">${esc(event.time)}</span>`
    + `<span><span class="kind ${esc(event.kind.replace(/ /g, '-'))}">${esc(event.kind)}</span></span><span class="p">${esc(event.port)}</span>`
    + `<span>${esc(event.message)}</span><span class="src" title="${esc(event.source)}">${esc(event.severity === 'info' ? '' : event.severity)}</span></div>`;
}

const isFaultEvent = (event) => /^Fault/.test(String(event.code || '')) || /^fault /.test(String(event.kind || ''));
// The Activity view is one full-width log (Zach, 2026-10-23: two columns were "very busy"). The host's fault memory
// is a filter of that log (its pending → confirmed → healed events, the memory's count above them); link health
// moved to the Computer's detail (detail section "links").
function renderActivity() {
  const act = S.act;
  if (!act) return '<div class="err muted">loading activity…</div>';
  const faults = S.actFilter === 'faults';
  // the utilities this page ran come first (newest first): the host logs no action runs of its own yet
  // WHY the host's RunStarted / RunEnded of a run this page started are left out: this page's own line says the same
  const mine = new Set(S.ran.map((line) => line.runId).filter(Boolean));
  const events = faults ? act.events.filter(isFaultEvent) : [...S.ran, ...act.events.filter((event) => !(event.type === 'run' && mine.has((event.args || {}).run_id)))];
  const active = act.faults.filter((fault) => fault.active).length;
  const filter = `<div class="seg small act-filter" role="radiogroup" aria-label="Show">${[['all', 'All events'], ['faults', 'Faults']].map(([id, label]) => `<button data-act-filter="${id}" role="radio" aria-checked="${S.actFilter === id}" class="${S.actFilter === id ? 'on' : ''}">${label}</button>`).join('')}</div>`;
  const memory = faults ? `<div class="act-note muted">The host's fault memory holds ${act.faults.length} fault${act.faults.length === 1 ? '' : 's'}${act.faults.length ? ` (${active} active)` : ''}: pending → confirmed → healed, as logged below.</div>` : '';
  return `<div class="act"><section><h2>Activity log <span class="act-count">· ${events.length} ${faults ? 'fault events' : 'events'}, newest first</span>${filter}</h2>${memory}`
    + (events.map(eventRow).join('') || `<div class="muted">${faults ? 'No fault events.' : 'No events yet.'}</div>`) + '</section></div>';
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
    + `<div class="msg">${esc(check.message || '—')}${check.code ? ` <span class="mono muted">${esc(check.code)}</span>` : ''}</div>`
    + (values || measurements ? `<table class="vt">${measurements}${values}</table>` : '')
    + `<div class="meta">received ${esc((check.received_iso || '').slice(11, 19) || '—')} · ${ago(check.age_s)} ago · ${esc(timing)}`
    + `${check.level !== check.effective_level ? ` · reported ${esc(check.level)}` : ''}<br>${esc(check.source)}</div></div>`;
}

const partItem = (part) => `<div class="part-row" data-go="${esc(part.entity)}" title="${esc(part.name)}">${icon(part.icon)}`
  + `<span class="pname">${esc(part.label)}</span><span class="pmsg">${esc(part.message)}</span>`
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

const HELPERS = { esc, icon, shape, pill, ago, kv, statusMark, eventRow, checkBlock };

// What a detail layout draws from, for the current selection.
function detailContext(byPath) {
  const group = !S.sel && S.selKey && S.built && S.built.byKey.has(S.selKey) ? S.built.byKey.get(S.selKey) : null;
  const node = group ? null : (byPath.get(S.sel) || S.data.tree);
  const detail = node && S.node && S.node.node.path === node.path ? S.node : null;
  // the row that stands for exactly this entity (not its owner, when the selection is a folded part)
  const rowKey = node && S.built ? S.built.byEntity.get(node.path) : null;
  const row = rowKey && S.built.byKey.get(rowKey).entity === node.path ? S.built.byKey.get(rowKey) : null;
  const isComputer = Boolean((node && node.kind === 'host') || (group && /(^|:)computer$/.test(group.key)));
  const parts = {
    head() {
      if (group) {
        return `<div class="d-head"><div class="d-title">${icon(group.icon)}<span>${esc(group.label)}</span></div>`
          + `<div class="d-msg">${group.status.hint ? `${esc(group.status.hint.level)} below · ${esc(group.status.hint.message)}` : esc(group.message || 'grouping row')}</div></div>`;
      }
      const title = row ? row.label : node.name;
      const hint = node.hint ? `<span class="muted"> · below: <a class="link" data-go="${esc(node.hint.origin)}">${esc(node.rollup.origin_name)}</a> is ${esc(node.hint.level)}</span>` : '';
      return `<div class="d-head"><div class="d-title">${icon(node.icon)}<span>${esc(title)}</span>${pill(node.level)}</div>`
        + `<div class="d-msg">${esc(node.message || '—')}${hint}</div>`
        + `<div class="d-path">${title !== node.name ? `${esc(node.name)} · ` : ''}${esc(node.path)}${node.hardware_id ? ` · ${esc(node.hardware_id)}` : ''}</div></div>`;
    },
    banners() {
      if (!node) return '';
      let html = '';
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
        + `<span class="pname">${esc(child.label)}</span><span class="pmsg">${esc(child.message || '')}</span>${statusMark(child)}</div>`).join('') || '<div class="muted">none</div>';
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
  const ctx = detailContext(byPath);
  S.layout = layoutFor(ctx.isComputer);
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
// WHY every click is a dry run first: the person reads exactly what would run (argv, steps, preconditions, the
// plugin's own confirm text) before anything can; a query then runs with one more click, anything else only from a
// confirm modal that repeats the danger (moves: a ticked box that states the physical precondition). The server
// runs a real action only with the dry run's token, from this page (io_diagnostics/server.py, docs/api-v1.md).
const STANDARD_ORDER = ['check_comms', 'check_firmware', 'update_firmware', 'update_config', 'clear_faults', 'set_id', 'reboot',
  'stop_all', 'open_console', 'calibrate', 'identify'];
const STANDARD = new Set(STANDARD_ORDER);
const DANGER_WORDS = {
  query: 'Reads only.',
  interrupts: 'Interrupts what runs: a stream drops, a board restarts or stops.',
  changes: 'Changes what the device stores: its configuration, id, faults or firmware.',
  moves: 'Makes hardware move: a motor spins, and a loaded joint can fall.',
};
// a precondition only a person can check (the host cannot read it passively): what the moves box states
const PHYSICAL = new Set(['joint_free', 'joint_clear', 'joints_clear', 'target_in_view']);
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
  identify: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
  more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/><rect x="2" y="5" width="20" height="14" rx="3"/>',
};
const utilIcon = (name) => `<svg class="u-ic" viewBox="0 0 24 24" aria-hidden="true">${UTIL_ICONS[STANDARD.has(name) ? name : 'more']}</svg>`;
const plainTitle = (title) => String(title || '').replace(/…$/, '');

// WHY Tools: a vendor's own console (tview, realsense-viewer) is something a person looks for by name (Zach, 2026-10-24: he
// did not find "Open console"); it sits right after Checks, outside Maintenance and never in Motion.
const TOOLS = new Set(['open_console']);
function utilityGroups(actions) {
  const order = (action) => (STANDARD.has(action.name) ? STANDARD_ORDER.indexOf(action.name) : STANDARD_ORDER.length);
  const sorted = [...actions].sort((a, b) => order(a) - order(b));
  const standard = sorted.filter((action) => STANDARD.has(action.name));
  return [
    { id: 'checks', title: 'Checks', items: standard.filter((action) => action.danger === 'query' && !TOOLS.has(action.name)) },
    { id: 'tools', title: 'Tools', items: standard.filter((action) => TOOLS.has(action.name)) },
    { id: 'maintenance', title: 'Maintenance', items: standard.filter((action) => action.danger !== 'query' && action.danger !== 'moves' && !TOOLS.has(action.name)) },
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

// set_id's new CAN id: what the person typed in its row when it is a whole number 1-126 other than the board's own id
// (the driver refuses anything else), else null. Every other action takes no new id.
function newIdOf(path, action) {
  if (action.name !== 'set_id') return null;
  const text = String(S.newIds.get(path) ?? '').trim();
  if (!/^\d+$/.test(text)) return null;
  const id = Number(text);
  return id >= 1 && id <= 126 && id !== nodeOf(path, action) ? id : null;
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
  const newId = newIdOf(path, action);
  if (name === 'set_id' && newId === null) return; // no dry run until the row holds a valid new id
  const util = { path, name, node: nodeOf(path, action), newId, phase: 'dry', dry: null, result: null, error: '', refused: '', ticked: false,
    title: action.title, danger: action.danger, owner: action.owner, label: entityLabel(path) };
  S.util = util;
  S.menuOpen = false; // the dry run reads in the sheet under the button
  S.runs.delete(path);
  render();
  const { status, data } = await postAct({ path, action: name, node: util.node, ...(newId !== null ? { new_id: newId } : {}), dry_run: true });
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
  S.runDetails = false;
  render();
  // the run repeats the dry run's new id: the server's token is bound to it (a token for CAN 7 never runs CAN 9).
  // WHY detach: the run is a terminal session that may wait for a typed answer, so the page does not hold the request
  const { status, data } = await postAct({ path: util.path, action: util.name, node: util.node, ...(util.newId !== null ? { new_id: util.newId } : {}), token: util.dry.token, detach: true });
  if (status === 202 && data.run && window.IORuns) {
    // WHY no "is it still the open one" return: the run is going; its end (syncRunUtilities) belongs to its entity's run
    // line (kept in S.runs while the viewer looks elsewhere) and to the Activity log either way
    util.runId = data.run.id;
    window.IORuns.ingest([data.run]);
    // a quiet query keeps the panel as the person left it; anything that changes or moves a device shows its terminal
    if (util.danger !== 'query') openRun(util.runId);
    render();
    return;
  }
  finishUtility(util, status, data);
}

// The utility is over: its result (a refusal, or the finished run's) becomes the run line and an Activity line.
function finishUtility(util, status, data) {
  util.phase = 'done';
  util.doneAt = Date.now();
  if (status === 200 && data.result) util.result = data.result; else { util.refused = data.refused || `HTTP ${status}`; util.error = data.error || ''; }
  const outcome = runOutcome(util);
  S.ran.unshift({ time: new Date(util.doneAt).toTimeString().slice(0, 8), kind: 'utility', port: util.label, message: `${plainTitle(util.title)}: ${outcome.text}`,
    severity: outcome.stopped ? 'warn' : outcome.ok ? 'info' : 'error', source: 'this page (POST /api/v1/act)', path: util.path, stamp: util.doneAt / 1000, runId: util.runId || null });
  S.ran = S.ran.slice(0, 50);
  S.actAt = 0; // the Activity log and the entity's diagnostics are read again now: the action may have changed them
  if (S.sel === util.path) { S.nodeAt = 0; loadNode(); }
  render();
}

// A utility whose run has ended on the host: read its result once and finish it. Called after every poll.
function syncRunUtilities() {
  for (const util of [S.util, ...S.runs.values()]) {
    if (!util || util.phase !== 'running' || !util.runId || util.syncing) continue;
    const run = window.IORuns.get(util.runId);
    if (!run || run.active) continue;
    util.syncing = true;
    window.IORuns.detail(util.runId)
      .then((detail) => finishUtility(util, 200, { result: { ...(detail.result || {}), run_id: detail.id } }))
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
  return node ? node.name : path;
}

// A run of another entity is kept with its entity (S.runs) while the viewer looks elsewhere, and comes back with it.
function stashRun(path) {
  if (S.util && S.util.path !== path) {
    if (S.util.phase === 'running' || S.util.phase === 'done') S.runs.set(S.util.path, S.util);
    S.util = null;
  }
  if (path && !S.util && S.runs.has(path)) { S.util = S.runs.get(path); S.runs.delete(path); }
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
  // WHY muted when not met: a precondition is not a diagnostic; warn / error colours are reserved for those (round 3)
  return `<ul class="pre">${rows.map((row) => `<li class="pre-${esc(row.state)}"><span class="st ${esc(row.state)}">${{ pass: '✓', fail: '✗' }[row.state] || '?'}</span>`
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
  const close = '<button class="btn" data-util-close>Cancel</button>';
  // asking / running: no panel and no live output; the row itself greys out with one quiet line ("Asking … what it would
  // run…", "Checking communication… 3 s"; Zach, 2026-10-23), and the panel comes back with the result
  if (util.phase === 'dry' || util.phase === 'running') return '';
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
  if (util.phase === 'done') foot = `<button class="btn" data-util-again>Dry run again</button>${close}`;
  else if (action.danger === 'query') foot = `<button class="btn primary query" data-util-run${runnable ? '' : ' disabled'}>Run ${esc(plainTitle(action.title))}</button>${close}`;
  else foot = `<button class="btn primary ${esc(action.danger)}" data-util-run${runnable ? '' : ' disabled'}>Continue…</button>${close}`;
  // the sheet stays small (round 3.5): what will run, its preconditions, the confirm text, Run / Cancel; the steps and
  // the plugin command line it runs through are one click further ("How it runs ▸")
  const how = `${stepsHtml(answer) ? `<div class="u-k">Steps</div><div class="u-v">${stepsHtml(answer)}</div>` : ''}`
    + `<div class="u-k">Through</div><div class="u-v"><code class="u-argv">${esc((result.argv || []).join(' '))}</code></div>`;
  return `<div class="u-panel">`
    + `<div class="u-panel-h">Dry run · nothing ran<span class="right muted">its token is good for ${esc(expires)}</span></div>`
    + `<div class="u-k">Will run</div><div class="u-v"><code class="u-cmd">${esc(command || 'in-process (no command line)')}</code>`
    + `${answer.command_kind ? ` <span class="tag recorded">${esc(answer.command_kind)}</span>` : ''}</div>`
    + `<div class="u-k">Preconditions</div><div class="u-v">${preconditionsHtml(preconditionRows(action, answer))}</div>`
    + `${confirm ? `<div class="u-k">Confirm</div><div class="u-v"><div class="confirm">${esc(confirm)}</div></div>` : ''}`
    + `${!runnable ? `<div class="banner blocked">◌ <span><b>${esc(action.reason || action.state)}</b></span></div>` : ''}`
    + `<button class="u-how-t" data-sheet-more aria-expanded="${S.sheetMore}">How it runs ${S.sheetMore ? '▾' : '▸'}</button>`
    + `<div class="u-how${S.sheetMore ? ' open' : ''}">${how}</div>`
    + `${util.phase === 'done' ? resultHtml(util) : ''}`
    + `<div class="u-foot">${foot}</div></div>`;
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
// whole flow: a [data-util=<name>] click is always a dry run; the panel (ctx.panel) carries Run / Continue…, and
// anything but a query runs only from the confirm modal (moves: its ticked box), with the dry run's token.
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
    h: { esc, utilIcon, plainTitle, gerund, shape },
    isActive,
    phase,
    runnable: (action) => RUNNABLE.has(action.state),
    // set_id names its new id in the row itself; its dry run stays disabled until the box holds a valid one
    needsId: (action) => action.name === 'set_id',
    waiting: (action) => action.name === 'set_id' && newIdOf(path, action) === null,
    newIdBox: (action) => (action.name === 'set_id' ? `<label class="u-newid"><span>New id (1-126)</span><input type="number" min="1" max="126" step="1" inputmode="numeric"`
      + ` data-util-newid value="${esc(S.newIds.get(path) ?? '')}" aria-label="New CAN id (1-126)"></label>` : ''),
    // the quiet line under a busy utility: '' when it is not asking or running
    busy: (action) => {
      const now = phase(action);
      if (now === 'dry') return `Asking ${action.owner} what it would run…`;
      if (now === 'running') return `${gerund(action.title)}… ${Math.max(0, Math.round((Date.now() - active.startedAt) / 1000))} s`;
      return '';
    },
    // round 3.5: the dry run reads in the sheet under the Utilities ▾ button, never inside the menu's rows
    panel: () => '',
  };
}

// The menu's rows: the active utility menu (u0's quiet rows by default: icon + title, the description as a tooltip,
// Checks / Maintenance / Motion) inside the header's dropdown.
function renderUtilities(actions, path) {
  if (!actions || !actions.length) return '';
  const menu = menus()[S.menu] || menus()[DEFAULTS.menu];
  if (!menu) return '<div class="muted">No utility menu loaded (utilities/*.js).</div>';
  try {
    return menu.html(utilityContext(actions, path)) || '';
  } catch (error) {
    console.error(`utility menu ${S.menu}:`, error);
    return `<div class="err">utility menu ${esc(S.menu)} failed: ${esc(error)}</div>`;
  }
}

// The entity header's tools: Utilities ▾ at the same place on every entity page (disabled where there are none), its
// menu, and the dry-run sheet anchored under it. The sheet is the dry run (what will run, preconditions, Run / Cancel);
// anything but a query then confirms in the modal, a safety gate and not feedback.
const CARET = '<svg class="ent-caret" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>';
function entityTools() {
  const detail = S.sel && !S.selKey && S.node && S.node.node.path === S.sel ? S.node : null;
  const actions = (detail && detail.actions) || [];
  const path = detail ? detail.node.path : null;
  const none = !actions.length;
  const open = S.menuOpen && !none;
  const title = S.selKey ? 'A grouping row has no utilities' : !detail ? 'Utilities (loading…)'
    : none ? 'No utilities for this entity' : `${actions.length} utilit${actions.length === 1 ? 'y' : 'ies'} · ${actions[0].owner || 'its plugin'} runs them · every one is a dry run first`;
  const button = `<button class="ent-util-btn${open ? ' on' : ''}" id="ent-util-btn" aria-haspopup="menu" aria-expanded="${open}" aria-controls="ent-menu"${none ? ' disabled' : ''} title="${esc(title)}">`
    + `<span>Utilities</span>${CARET}</button>`;
  const menu = open ? `<div class="ent-menu" id="ent-menu" role="menu" aria-label="Utilities">${renderUtilities(actions, path)}</div>` : '';
  const util = S.util && S.util.path === path ? S.util : null;
  const sheet = !open && util && ['dry', 'shown', 'confirm'].includes(util.phase) ? sheetHtml(util, actions) : '';
  return `<div class="ent-tools">${button}${menu}${sheet}</div>`;
}

function sheetHtml(util, actions) {
  const action = actions.find((item) => item.name === util.name);
  if (!action) return '';
  const danger = action.danger !== 'query' ? `<span class="danger ${esc(action.danger)}">${esc(action.danger)}</span>` : '';
  const body = util.phase === 'dry' ? `<div class="ent-asking u-busy">Asking ${esc(action.owner)} what it would run…</div>` : dryRunPanel(action);
  return `<div class="ent-sheet" role="dialog" aria-label="${esc(`Dry run: ${plainTitle(action.title)}`)}">`
    + `<div class="ent-sheet-h">${utilIcon(action.name)}<b>${esc(plainTitle(action.title))}</b>${danger}</div>${body}</div>`;
}

// The quiet one-line run status under the entity header: "Updating firmware on moteus-c1 · CAN 21… 42 s" while it runs
// (muted, never a warning colour), then its result ("Firmware updated to 1.1.2 · just now · Details ▸"); Details opens
// the run's records and output tail. Only a failure is a diagnostic-level line, the one place colour is allowed.
function runLine() {
  const path = S.selKey ? null : S.sel;
  const util = S.util && S.util.path === path && (S.util.phase === 'running' || S.util.phase === 'done') ? S.util : null;
  if (!util) return '';
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
    // WHY the utility's own icon, not a warn / error shape: those shapes mean a diagnostic result (round 3); moves gets
    // the strongest NEUTRAL treatment instead (a heavy frame, the ticked box, an inverted button)
    + `<div class="body"><div class="m-danger">${utilIcon(action.name)}<b>${esc(DANGER_WORDS[action.danger] || 'Needs your confirmation: its danger class is not one this page knows.')}</b></div>`
    + `<div class="confirm">${esc(answer.confirm || action.confirm)}</div>`
    + `${util.name === 'set_id' && util.newId !== null ? `<div class="m-newid">CAN ${esc(util.node)} → CAN ${esc(util.newId)}</div>` : ''}`
    + `<div class="m-runs">Runs <code>${esc(commandOf(answer) || 'in-process')}</code></div>`
    + `<div class="m-pre-h">Preconditions</div>${preconditionsHtml(rows)}`
    + `${moves ? `<label class="m-tick"><input type="checkbox" data-util-tick${util.ticked ? ' checked' : ''}><span>I checked: ${esc(tick)}</span></label>` : ''}`
    + `</div><footer><span class="note">${esc(action.owner)} runs it once you press the button; the dry run's token is good for ${esc(Math.round((util.dry.window_s || 300) / 60))} min.</span>`
    + `<button class="btn" data-util-cancel>Cancel</button>`
    + `<button class="btn primary ${esc(action.danger)}" data-util-confirm${moves && !util.ticked ? ' disabled' : ''}>${esc(plainTitle(action.title))}</button></footer></div>`;
  if (html !== S.modalHtml) { back.innerHTML = html; S.modalHtml = html; }
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
  return ((detail && detail.checks) || []).filter((check) => PROBLEM.has(check.effective_level)).map((check) => {
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
    + `<div class="why-msg">${esc(why.message)}</div>${whyLines(why)}</div>`).join('');
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
    + '<button class="pop-open" data-close="1">Open RobotDx</button>'
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
  S.byPath = byPath;
  S.built = IOView.build(S.data, S.structure, { ...structureOptions(), query: S.query, sort: S.sort, changes: changesOf(S.act) });
  $('clock').textContent = S.data.clock.iso ? `${S.data.clock.iso.slice(5, 10)}  ${S.data.clock.time}` : '—';
  const fixture = S.data.fixture;
  $('mode').textContent = fixture ? `Replay ${fixture.name} · ${fixture.label} (recorded)` : `Live · ${S.data.host.name}`;
  const skipped = S.data.host.sources.filter((s) => s.state === 'skipped');
  $('sources').hidden = !skipped.length;
  $('sources').innerHTML = skipped.map((s) => `<span class="tag simulated" title="${esc(s.note)}">${esc(s.name)} skipped</span>`).join('');
  document.querySelectorAll('#views button').forEach((b) => b.classList.toggle('on', b.dataset.view === S.view));
  const treeView = S.view === 'tree';
  const tableView = S.view === 'table';
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
  $('tree-host').hidden = !treeView;
  $('left-other').hidden = treeView;
  if (treeView) renderTreePane();
  else $('left-other').innerHTML = S.view === 'table' ? renderTable() : renderActivity();
  const noSearch = treeView && NO_QUERY.has(S.variant);
  $('search').disabled = noSearch;
  $('search').placeholder = noSearch ? `No search in ${S.variant} · try Table` : 'Search entities, ids, ports…';
  $('search').closest('.search').classList.toggle('off', noSearch);
  // WHY drawn through a layout's ctx.draw: an unchanged detail is not rewritten every second (selection, focus, scroll)
  renderDetail(byPath);
  renderChip();
  renderModal();
  renderStatusbar();
  updateRunView();
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
  const filter = t.closest('[data-act-filter]');
  if (filter) { S.actFilter = filter.dataset.actFilter; return render(); }
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
  const showRun = t.closest('[data-run-open]');
  if (showRun) { openRun(showRun.dataset.runOpen); return undefined; }
  if (t.closest('[data-util-tick]')) return undefined; // its change event records the tick
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
$('history-window').addEventListener('change', (event) => { const seconds = Number(event.target.value); if (seconds > 0) setWindow(seconds); });
// the scroll position of the drill step on screen (restored when back / forward returns to it); a scroll while another
// step is still loading belongs to no step
$('detail').addEventListener('scroll', () => { if (S.drawnAt && S.drawnAt === drillAt()) S.scrolls.set(S.drawnAt, $('detail').scrollTop); }, { passive: true });
$('structure-toggles').addEventListener('change', (event) => {
  const box = event.target.closest('[data-opt]');
  if (box) setToggle(box.dataset.opt, box.checked);
});
document.addEventListener('change', (event) => {
  if (event.target.closest('[data-util-tick]') && S.util) { S.util.ticked = event.target.checked; render(); }
  if (event.target.closest('[data-setup-rig]') && S.setup) { S.setup.rig = event.target.value; render(); }
});
document.addEventListener('input', (event) => {
  if (event.target.closest('[data-setup-reason]') && S.setup) { S.setup.reason = event.target.value; render(); return; }
  const box = event.target.closest('[data-util-newid]');
  const action = box && S.node && (S.node.actions || []).find((item) => item.name === 'set_id');
  if (!action) return;
  const path = S.node.node.path;
  S.newIds.set(path, box.value);
  // a dry run shown for another new id no longer says what would run: close it, the person dry-runs the new one
  const util = S.util;
  if (util && util.path === path && util.name === 'set_id' && util.phase !== 'running' && util.newId !== newIdOf(path, action)) S.util = null;
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
  if (event.key !== 'Escape') return;
  // one layer at a time, innermost first: the confirm, the Utilities menu, its dry-run sheet, the setup edit, the
  // chip, View options, then the inspector
  if (S.util && S.util.phase === 'confirm') S.util.phase = 'shown';
  else if (S.menuOpen) { S.menuOpen = false; render(); $('ent-util-btn') && $('ent-util-btn').focus(); event.preventDefault(); return; }
  else if (S.util && (S.util.phase === 'shown' || S.util.phase === 'dry') && S.util.path === S.sel) S.util = null;
  else if (S.setup && S.setup.phase === 'confirm') S.setup = null;
  else if (S.chip) S.chip = false;
  else if (S.vo) setViewOptions(false);
  else if (S.view === 'table' && S.inspOpen && !S.inspPinned) S.inspOpen = false;
  render();
});

async function boot() {
  // index.html's inline script already set data-skin / data-mode from storage, so the first paint is right
  const [skins, structures, detailLayouts, utilityMenus, runEntries] = await Promise.all([readManifest('skins/index.json', 'skins'),
    readManifest('view/structure/index.json', 'structures'), readManifest('detail/index.json', 'layouts'), readManifest('utilities/index.json', 'menus'),
    readManifest('runs/index.json', 'runs')]);
  S.skins = skins.entries.length ? skins.entries : [{ id: DEFAULTS.skin, name: 'shadcn/ui', file: 'shadcn.css', primary: true }];
  await Promise.all([
    ...S.skins.map((skin) => loadStylesheet(`skins/${skin.file}`, skin.id)),
    ...structures.entries.map((entry) => loadScript(`view/structure/${entry.file}`)),
    ...detailLayouts.entries.map((entry) => loadScript(`detail/${entry.file}`)),
    ...utilityMenus.entries.map((entry) => loadScript(`utilities/${entry.file}`)),
    ...runEntries.entries.map((entry) => loadScript(`runs/${entry.file}`)),
  ]);
  setupSkins(skins.default);
  setupStructures(structures);
  setupSwitcher();
  setupPrototype(detailLayouts.default, utilityMenus.default, runEntries.default);
  S.inspPinned = readPref(PREFS.inspectorPinned) === '1';
  if (S.view === 'table' && (S.sel || S.selKey) && (S.drill.diag || S.drill.page)) S.inspOpen = true;
  setupPanes();
  loadSettings();
  tick();
  setInterval(tick, 1000);
}
boot();
