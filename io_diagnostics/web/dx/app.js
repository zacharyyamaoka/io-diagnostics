/* RobotDx on data-ui: the shell (docs/peps/0001-robotdx-on-data-ui.md §12).

   Zach, 2026-10-26: "reimplement robotdx using data-ui … explore the raw data. AND we also want this ability to make
   dashboards … at any moment you can just kinda like 'rewind' and just go back in time". So: an app rail, one clock
   for the whole page (Rerun's time panel), Explore (the tree and the selected entity, both data-ui views over the live
   datasets of feed.js), Activity, Dashboards (boards.js), Runs and Settings. Every panel is a stock <data-views> or
   <data-dashboard>; this file only routes, binds datasets and keeps the URL.

   Read-only by construction: the only requests that are not a GET are watch.js's heartbeat (PEP §12.2) and the person's own
   dashboard files (boards.js, add.js: PEP §14, §17). Every write the host offers (utilities, configuration, the expected setup,
   settings) stays on the classic page, linked per entity. */

import { LiveClock, LiveDataset, parseBase, stringifyBase, loadDataset, connectSSE, connectPoll, makeResizable, bindRailSelect, esc, fmtTime, loadView } from '../vendor/data-ui/data-ui.js';
import '../vendor/data-ui/dashboard.js';
import { RobotDxFeed, bindingOf } from './feed.js';
import { startHeartbeat } from './watch.js';
import { mountDashboards } from './boards.js';
import { mountTree } from './tree.js';
import { installAddToDashboard } from './add.js';

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
// this browser's choices (a private window: this visit only)
const store = {
  get(k) { try { return localStorage.getItem(k); } catch (_) { return null; } },
  set(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v); } catch (_) { /* private window: this visit only */ } },
};

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
// An entity's tabs (Zach, 2026-10-28: "on each entity we had these tabs to split up different considerations (Diagnostics/State/
// Configuration/Log/Utilities) ... I still want that tabbed model on each entity"; PEP 0001 §16): the overview first, then the
// classic entity page's five, under the classic page's own ids (?page=), each a dashboard of its own over the page's datasets.
const TABS = [
  { id: 'overview', title: 'Overview', file: 'explore.base' },
  { id: 'diagnostics', title: 'Diagnostics', file: 'entity-diagnostics.base', kinds: ['checks', 'levels'] },
  { id: 'state', title: 'State', file: 'entity-state.base', kinds: ['peek'] },
  { id: 'config', title: 'Configuration', file: 'entity-config.base', kinds: ['config'] },
  { id: 'log', title: 'Log', file: 'entity-log.base', kinds: [] },
  { id: 'utilities', title: 'Utilities', file: 'entity-utilities.base', kinds: ['actions'] },
];
const TAB_KEY = 'robotdx.dx.entity-tab.';   // + the entity's kind: the tab last shown for that kind (the classic page keeps it so too)

export function readUrl() {
  const q = new URLSearchParams(location.search);
  const go = DESTS.includes(q.get('go')) ? q.get('go') : 'explore';
  return { go, sel: q.get('sel') || '', board: q.get('board') || '', page: TABS.some((x) => x.id === q.get('page')) ? q.get('page') : '' };
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

/** Each drawn <data-dashboard>'s datasets: {name: the file its .base declares, resolved}. WHY kept: the page hands every view of a
    kind one shared dataset (§12.1), whose url says one binding, so the file a panel reads is the one its dashboard declares
    (Add to dashboard saves that one: add.js). */
const filesOf = new WeakMap();
/** The file the dashboard around `view` declares for its panel's dataset (data-ui's el.dashboardPanel names it), else null. */
function declaredFile(view) {
  const panel = view && view.dashboardPanel;
  const files = panel && view.closest ? filesOf.get(view.closest('data-dashboard')) : null;
  return (files && files[panel.dataset]) || null;
}

/** Hand a <data-dashboard> the page's clock for good. WHY (data-ui 5b4e793, sim2real gap #13: "a new document gets a new
    clock"): load() of a new document retires the clock the dashboard holds (pause, let go of every dataset, stop) and makes
    one of its own, so the views inside it would follow a clock the tree and the time bar do not. This page has ONE clock for
    the tree, the time bar and every dashboard (PEP 0001 §12): an own `clock` the dashboard reads and cannot replace, and
    openBase puts following back after the load. Asked of data-ui: retire only a clock the dashboard made itself. */
function holdClock(el) {
  const own = Object.getOwnPropertyDescriptor(el, 'clock');
  if (own && own.get) return;
  Object.defineProperty(el, 'clock', { configurable: true, enumerable: true, get: () => clock, set: () => { /* the page's, kept */ } });
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
  filesOf.set(el, Object.fromEntries(Object.entries(all).map(([name, d]) => [name, new URL(d.data, baseUrl).href])));
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
  holdClock(el);
  el.baseText = text;
  const following = clock.following;
  el.load(base, datasets, { keepState });
  rebind();   // a dashboard with no live dataset sends no data-dashboard-live, and still let go of the page's
  // the load of a new document paused the page's clock (holdClock's WHY): a reader who was following still follows
  if (following && !clock.following) clock.goLive();
  return {
    base,
    release() { for (const b of used) feed.release(b.kind, b.path); for (const c of closers) c.close(); used.length = 0; closers.length = 0; },
  };
}

/** Whether a drawn dashboard has its own playback card (then the page's time bar steps aside). */
const hasPlayback = (base) => (base.views || []).some((v) => v.type === 'playback');
export function showTimeBar(on) { timeBar.hidden = !on; drawLive(); }

// ---- the playback bar to compare (Zach, 2026-10-28: "Playback bar: I am not sure what I will like more. please give the option to
// switch between today's bar, rerun simple and foxglove bar"): data-ui's own proposals (its playback.variant: bar, rerun, foxglove),
// one temporary bottom-right picker that switches live and remembers the pick on this device (memory feedback_prototype_switch_in_app)
const BARS = [['bar', "Today's bar"], ['rerun', 'Rerun · simple'], ['foxglove', 'Foxglove']];
const BAR_KEY = 'robotdx.dx.playback-variant';
const barPick = document.getElementById('dx-playback-variant');
const liveBtn = document.getElementById('dx-live');
function barVariant() { const v = store.get(BAR_KEY); return BARS.some(([id]) => id === v) ? v : 'bar'; }
function drawTimeBar() { timeBar.base = { views: [{ type: 'playback', name: 'Time', playback: { variant: barVariant() } }] }; drawLive(); }
barPick.innerHTML = BARS.map(([id, name]) => `<option value="${id}">${esc(name)}</option>`).join('');
barPick.addEventListener('change', () => { store.set(BAR_KEY, barPick.value === 'bar' ? null : barPick.value); drawTimeBar(); });
// WHY a LIVE of the page's own beside Rerun's and Foxglove's bars: data-ui's proposals were made before its live tier and have no
// way back to the newest data (today's bar has ● LIVE); asked of data-ui (PEP 0001 §16)
const lagText = (s) => (s < 60 ? `${s.toFixed(1)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`);
let liveDrawnAt = 0;
function drawLive() {
  liveDrawnAt = performance.now();
  drawClockWord();
  const show = barVariant() !== 'bar' && clock.live && !timeBar.hidden;
  liveBtn.hidden = !show;
  if (!show) return;
  liveBtn.setAttribute('aria-pressed', String(clock.following));
  const text = clock.following ? '● LIVE' : `○ LIVE −${lagText(clock.lag)}`;
  if (liveBtn.textContent !== text) liveBtn.textContent = text;
}
clock.addEventListener('live', () => { if (performance.now() - liveDrawnAt > 200) drawLive(); });
clock.addEventListener('state', drawLive);
liveBtn.addEventListener('click', () => { clock.goLive(); drawLive(); });

// ---- the timeline: a panel that rises from the bottom (Zach, 2026-10-29: "The playback/rewind bar becomes a panel that rises
// from the bottom of the page, collapses, and is brought back from the bottom status bar, like the Terminal panel on the classic
// page ... It is live by default, like a system monitor, and you can rewind at any moment. Its height is draggable and remembered,
// and it stays collapsed or open across reloads"). The classic Terminal panel's conventions (web/runs/r1.js): its top edge drags
// (remembered, double-click resets, arrow keys move it by 16 px, 120 px to the window less 220), a hide chevron in its head, a
// status-bar toggle that says what it does (aria-pressed / aria-expanded / aria-controls), Ctrl+` both ways, open or not kept.
// Inside: the page's one time bar, and the rack's history under it as a system monitor draws it (data-ui's levels and plot over
// the summary dataset: the worst level per bin, the problem counts), both as of the clock's t. Collapsing it never moves the
// clock: the status bar's toggle says LIVE, or how far behind.
const TL_OPEN = 'robotdx.dx.timeline.open';
const TL_HEIGHT = 'robotdx.dx.timeline.height';
const TL_DEFAULT = 200, TL_MIN = 120;
const timeline = document.getElementById('dx-timeline');
const tlSash = timeline.querySelector('.dx-tl-sash');
const tlToggle = document.getElementById('dx-tl-toggle');
const tlClock = document.getElementById('dx-tl-clock');
let tlHeight = TL_DEFAULT;
function tlApply(height, persist) {
  const max = Math.max(TL_MIN, innerHeight - 220);
  tlHeight = Math.round(Math.max(TL_MIN, Math.min(height, max)));
  timeline.style.setProperty('--dx-tl-h', `${tlHeight}px`);
  if (persist) store.set(TL_HEIGHT, String(tlHeight));
  return tlHeight;
}
function tlOpen(open) {
  timeline.hidden = !open;
  tlToggle.setAttribute('aria-pressed', String(open));
  tlToggle.setAttribute('aria-expanded', String(open));
  // WHY only a closed panel is written: open is the default, so a reader who never closes it never has a stored choice
  store.set(TL_OPEN, open ? null : '0');
}
/** The status bar's word for the clock: ● LIVE while following, else how far behind (the toggle says it with the panel closed). */
function drawClockWord() {
  const text = !clock.live ? '' : clock.following ? '● LIVE' : `○ −${lagText(clock.lag)}`;
  if (tlClock.textContent !== text) tlClock.textContent = text;
  tlClock.dataset.following = String(Boolean(clock.live && clock.following));
}
tlApply(Number(store.get(TL_HEIGHT)) || TL_DEFAULT, false);
tlOpen(store.get(TL_OPEN) !== '0');
tlToggle.addEventListener('click', () => tlOpen(timeline.hidden));
timeline.querySelector('[data-tl="hide"]').addEventListener('click', () => { tlOpen(false); tlToggle.focus(); });
document.addEventListener('keydown', (e) => {
  if (e.ctrlKey && !e.altKey && !e.metaKey && (e.key === '`' || e.code === 'Backquote')) { e.preventDefault(); tlOpen(timeline.hidden); }
}, true);
{
  let drag = null;
  tlSash.addEventListener('pointerdown', (e) => {
    if (e.button !== 0) return;
    drag = { id: e.pointerId };
    tlSash.setPointerCapture(e.pointerId);
    document.body.classList.add('dx-resizing');
    e.preventDefault();
  });
  tlSash.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    drag.height = tlApply(timeline.getBoundingClientRect().bottom - e.clientY, false);
  });
  const end = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (drag.height !== undefined) tlApply(drag.height, true);
    drag = null;
    document.body.classList.remove('dx-resizing');
  };
  tlSash.addEventListener('pointerup', end);
  tlSash.addEventListener('pointercancel', end);
  tlSash.addEventListener('dblclick', () => { store.set(TL_HEIGHT, null); tlApply(TL_DEFAULT, false); });
  tlSash.addEventListener('keydown', (e) => {
    if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
    tlApply(tlHeight + (e.key === 'ArrowUp' ? 16 : -16), true);
    e.preventDefault();
  });
  addEventListener('resize', () => tlApply(tlHeight, false));
}
/** The rack's history in the panel: the worst level per bin, and the problem counts, over the host's window ending at t. */
function drawHistory() {
  const at = `${Math.round(feed.host.window || 900)}s`;
  const health = document.getElementById('dx-tl-health');
  const counts = document.getElementById('dx-tl-counts');
  health.base = { properties: { level: { displayName: 'Rack' } }, views: [{ type: 'levels', name: 'Rack health', at,
    levels: { state: 'level', label: 'health', order: ['ok', 'warn', 'unknown', 'stale', 'error'], tones: { ok: 'good', warn: 'warn', unknown: 'muted', stale: 'muted', error: 'bad' }, bar: 3 } }] };
  health.data = feed.dataset('summary');
  counts.base = { views: [{ type: 'plot', name: 'Problems', at, plot: { y: ['errors', 'warns', 'stales', 'unknowns'] } }] };
  counts.data = feed.dataset('summary');
}

// ---- Explore: the tree and the selected entity -------------------------------------------------------------------------------

// The Table mode of the tree pane: data-ui's (the tree itself is the classic page's own: tree.js, PEP 0001 §16)
const TABLE_BASE = {
  views: [{
    type: 'table', name: 'Table',
    // WHY these filters: an OS device no driver claims (a keyboard, a mouse) stays hidden unless it reports a problem
    // of its own, and a hidden row never hides a fault (E11)
    filters: 'claimed == true || rank > 0',
    order: ['name', 'health', 'message', 'id_text', 'rate', 'heard', 'checks', 'faults', 'kind'],
    sort: [{ property: 'rank', direction: 'DESC' }],
  }],
};

const FOLD = 'robotdx.dx.tree.folded';

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

/** A tab's .base for one entity: the overview is exploreText's (empty panels left out); the State tab gets a Signals tab per
    value as the overview does; every other tab is its template with {path} filled in, every panel kept (a tab is always there). */
function tabText(tab, tpl, path, entity) {
  if (tab.id === 'overview') return exploreText(tpl, path, entity);
  if (tab.id === 'log') return logText(logVariant(), tpl, path, logScope());
  if (tab.id !== 'state') return tpl.replaceAll('{path}', path);
  const base = parseBase(tpl.replaceAll('{path}', path));
  const keys = entity ? [...entity.keys.entries()] : [];
  const numeric = keys.filter(([k]) => !['mode', 'fault'].includes(k)).length ? keys.filter(([k]) => !['mode', 'fault'].includes(k)) : keys;
  base.views = base.views || [];
  for (const [key, info] of numeric) base.views.push({ type: 'plot', name: info.unit ? `${key} (${info.unit})` : key, from: 'samples', at: '30s', plot: { y: ['value'], series: 'key', only: [key], unit: info.unit || undefined } });
  base.layout.panels = base.layout.panels.map((p) => (p.key === 'signals' ? { ...p, views: numeric.map(([key, info]) => (info.unit ? `${key} (${info.unit})` : key)) } : p))
    .filter((p) => p.key !== 'signals' || p.views.length);   // no values yet: the cards say so; the plots come with the first value
  return stringifyBase(base);
}
const templates = new Map();   // a tab's template text, read once

// ---- the Log tab's toolbar: five one-line proposals (Zach, 2026-10-29: "If you are using data-ui then the ways of doing this are
// already quite supported. for example we don't want to tool bar to split onto two lines"). The classic Log tab wraps: its count,
// level segment, Raw, search and Live, then "Log of CAN 13 + children / this only / + path to host" on a second line. Here every
// proposal is data-ui's one-row bar (CONTRACT §15: toolbar="bar", never wrapping; its presets are attributes) and the log's
// controls are data-ui's own things: the level floor a view, a facet or a clause typed in the query box (level:>=2), the scope a
// view, a filter clause or the breadcrumb, the raw lines a channel the filters keep or drop (-channel:raw), the count data-ui's
// status line, and Live the page's one LIVE
// (the timeline, the status bar: Rerun's single way back). A temporary bottom-right picker switches them live and remembers the
// pick on this device; variant 1 is the default (memory feedback_prototype_switch_in_app, feedback_default_to_variants).
const LOGS = [['overflow', '1 · Overflow menu'], ['chip', '2 · Scope as a chip'], ['search', '3 · Search on focus'], ['crumb', '4 · Scope in the breadcrumb'], ['facet', '5 · Level as a facet']];
const LOG_KEY = 'robotdx.dx.log-toolbar';
const LOG_SCOPE = 'robotdx.dx.log-scope';
// the classic log's floors (web/activity/core.js FLOORS, its labels) and scopes (web/activity/log.js SCOPE_MODES)
const FLOORS = [['Debug+', 0], ['Info+', 1], ['Warn+', 2], ['Error+', 3]];
const FLOOR_DEFAULT = 'Info+';
const SCOPES = [['children', '+ children'], ['self', 'this only'], ['host', '+ path to host']];
// each proposal's data-ui bar (CONTRACT §15): views as the chip (Bases) or tabs (Notion); promote="" leaves Display the one menu
// (Shopify); filters="expanded" keeps the clauses off a second row (no pill row); search "icon" folds the box until it is used;
// labels "icons" keeps every tool to its icon. WHY no query-tokens: a token for the scope wrapped the query box onto a second and
// third line, on a board's Log at 1280 px and at 390 (data-ui's .dv-qbox wraps; written down for data-ui)
const LOG_BARS = {
  overflow: { views: 'dropdown', promote: '', filters: 'expanded', search: 'box', labels: 'auto' },
  chip: { views: 'dropdown', promote: '', filters: 'expanded', search: 'box', labels: 'auto' },
  search: { views: 'dropdown', promote: 'filter', filters: 'expanded', search: 'icon', labels: 'icons' },
  crumb: { views: 'tabs', promote: 'filter', filters: 'expanded', search: 'box', labels: 'auto' },
  facet: { views: 'tabs', promote: '', filters: 'expanded', search: 'box', labels: 'auto' },
};
const FLOOR_VIEWS = new Set(['overflow', 'search', 'crumb']);   // the proposals whose views are the floors
const logPick = document.getElementById('dx-log-variant');
function logVariant() { const v = store.get(LOG_KEY); return LOGS.some(([id]) => id === v) ? v : 'overflow'; }
function logScope() { const v = store.get(LOG_SCOPE); return SCOPES.some(([id]) => id === v) ? v : 'children'; }
logPick.innerHTML = LOGS.map(([id, name]) => `<option value="${id}">${esc(name)}</option>`).join('');
logPick.value = logVariant();
logPick.addEventListener('change', () => { store.set(LOG_KEY, logPick.value === 'overflow' ? null : logPick.value); dispatchEvent(new CustomEvent('dx-log-variant')); });

/** The scope as one Bases expression (web/activity/core.js inScope): the entity, what is below it, and the path up to the host.
    WHY lineage (feed.js: each row's path and the paths above it): "+ children" is then one clause with a short token form,
    lineage:<path>, where path == p || path.startsWith(p + "/") drew as a token that took the query box onto a second line. */
function scopeExpr(path, scope) {
  const q = (s) => JSON.stringify(s);
  if (scope === 'self') return `path == ${q(path)}`;
  const below = `lineage.contains(${q(path)})`;
  if (scope === 'children') return below;
  const up = [];
  for (let p = path; p.includes('/');) { p = p.slice(0, p.lastIndexOf('/')); up.push(`path == ${q(p)}`); }
  return [below, ...up].join(' || ');
}

/** The Log tab's .base in one proposal: the template's table as the floors (or the scopes), with the scope, the default floor
    and the facets where that proposal keeps them. */
function logText(variant, tpl, path, scope) {
  const base = parseBase(tpl.replaceAll('{path}', path));
  const table = (base.views || []).find((v) => v.type === 'table');
  const view = (name, filters) => ({ ...table, name, ...(filters ? { filters } : {}) });
  const floors = (scoped) => FLOORS.map(([name, n]) => view(name, scoped ? `(${scoped}) && sev >= ${n}` : `sev >= ${n}`));
  const entry = base.datasets.events;
  const ds = typeof entry === 'string' ? { data: entry } : { ...entry };
  const scopes = () => SCOPES.map(([id, label]) => view(label, scopeExpr(path, id)));
  if (variant === 'facet') {
    // the level and the raw lines in the dashboard's own rail (a stock <data-facets>, CONTRACT §7, §17); the scopes as tabs (the
    // classic chip's segment)
    base.views = scopes();
    ds.filters = `sev >= ${FLOORS.find(([n]) => n === FLOOR_DEFAULT)[1]}`;
    ds.facets = [
      { as: 'popular', label: 'Level', options: FLOORS.map(([name, n]) => ({ label: name, filter: `sev >= ${n}` })) },
      { property: 'channel', as: 'toggle', value: 'activity', text: 'Hide raw lines', label: 'Raw lines' },
    ];
  } else if (variant === 'chip') {
    // the scope is the view chip; the level and the raw lines are typed in the query box (level:>=2, -channel:raw), with data-ui's
    // hints: the classic search's own words (level:warn), as Shopify's query builder takes them
    base.views = scopes();
  } else if (variant === 'crumb') {
    // the scope is the breadcrumb's, in each view's own filter: nothing of it on the bar
    base.views = floors(scopeExpr(path, scope));
  } else {
    // the scope is a clause of the all-views filter: kept in Display (1), behind Filter (3)
    base.views = floors();
    ds.filters = scopeExpr(path, 'children');
  }
  base.datasets.events = ds;
  const panel = { ...base.layout.panels[0], views: base.views.map((v) => v.name) };
  delete panel.view;
  base.layout.panels = [panel];
  return stringifyBase(base);
}

/** After the Log tab's dashboard is drawn: its table gets the proposal's bar, the default floor, and the detail says which. */
function dressLog(detail, variant, { fresh }) {
  detail.dataset.logVariant = variant;
  const t = detail.querySelector('#dx-detail-log');
  if (!t) return;
  for (const [name, value] of Object.entries(LOG_BARS[variant])) if (t.getAttribute(name) !== value) t.setAttribute(name, value);
  if (fresh && FLOOR_VIEWS.has(variant) && typeof t.selectView === 'function') t.selectView(FLOOR_DEFAULT);
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
      <aside id="dx-tree-pane" aria-label="Entities"></aside>
      <section class="dx-detail-wrap">
        <nav id="dx-tabs" class="dx-tabs" role="tablist" aria-label="The entity's tabs" hidden>${TABS.map((t) => `<button type="button" role="tab" data-dx-tab="${t.id}" aria-selected="false">${esc(t.title)}</button>`).join('')}</nav>
        <data-dashboard id="dx-detail" no-url></data-dashboard>
      </section>
    </div>
  </div>`;
  const pane = root.querySelector('#dx-tree-pane');
  const detail = root.querySelector('#dx-detail');
  const fold = root.querySelector('#dx-tree-fold');
  const crumbs = root.querySelector('#dx-crumbs');
  const classic = root.querySelector('#dx-classic');
  const keep = root.querySelector('#dx-keep');
  const tabBar = root.querySelector('#dx-tabs');
  tabBar.addEventListener('click', (e) => {
    const b = e.target.closest('[data-dx-tab]');
    if (b && current.path && b.dataset.dxTab !== current.page) go('explore', { sel: current.path, page: b.dataset.dxTab });
  });
  // WHY: Zach always wants side panels that drag, remember, reset and fold (memory feedback_resizable_panels)
  const sizer = makeResizable(pane, { edge: 'right', min: 220, max: 640, keep: 420, key: 'dx-tree', label: 'the entity tree' });
  const setFold = (on) => { pane.hidden = on; fold.setAttribute('aria-pressed', String(on)); store.set(FOLD, on ? '1' : null); };
  fold.addEventListener('click', () => setFold(!pane.hidden));
  setFold(store.get(FOLD) === '1');
  // the Table mode: data-ui's table of the entities; a click on a row selects it (the URL and Back follow), never a peek
  const table = document.createElement('data-views');
  table.id = 'dx-tree-table';
  table.setAttribute('no-url', '');
  table.setAttribute('toolbar', 'compact');
  table.base = TABLE_BASE;
  table.data = feed.dataset('entities');
  table.openPeek = (id) => go('explore', { sel: String(id) });
  // the tree: the classic page's own (tree.js), drawn as of the clock's t
  const tree = mountTree(pane, { feed, clock, table, onSelect: (path) => go('explore', { sel: path }) });
  const mark = document.createElement('style');
  root.append(mark);
  const markTable = (path) => { mark.textContent = path ? `#dx-tree-table tr[data-id="${CSS.escape(path)}"] { background: var(--dv-hover); box-shadow: inset 3px 0 0 var(--dv-accent); }` : ''; };

  /** The entity's name and path; on its Log in proposal 4, the Log's scope beside them ("Log of + children", the classic chip). */
  function drawCrumbs(path, e, page) {
    crumbs.innerHTML = path ? `<b>${esc((e && e.name) || path)}</b> <span>${esc(path)}</span>` : 'Pick an entity in the tree';
    if (path && page === 'log' && logVariant() === 'crumb') {
      crumbs.insertAdjacentHTML('beforeend', ` <label class="dx-crumb-scope">Log of <select data-log-scope aria-label="Log scope" title="Whose lines the Log shows">${SCOPES.map(([id, label]) => `<option value="${id}"${id === logScope() ? ' selected' : ''}>${esc(label)}</option>`).join('')}</select></label>`);
    }
  }

  let current = { path: null, release: null, seq: 0, hold: null, sig: '' };
  // what decides Explore's panels for an entity: its State keys (in order) and whether it has settings
  const sigOf = (entity) => (entity ? `${[...entity.keys.keys()].join(',')}|${Boolean(entity.node && (entity.node.config || []).length)}` : '');
  async function select(path, urlPage = '') {
    const seq = ++current.seq;
    tree.select(path);
    markTable(path);
    const e = path ? feed.dataset('entities').latestAt(Infinity, path) : null;
    // the tab: the URL's, else the one last shown for this kind of entity, else the overview; the one shown is remembered
    const kindKey = `${TAB_KEY}${(e && e.kind) || 'entity'}`;
    const page = path ? (urlPage || (TABS.some((t) => t.id === store.get(kindKey)) ? store.get(kindKey) : 'overview')) : '';
    if (path) store.set(kindKey, page);
    const tab = TABS.find((t) => t.id === page) || TABS[0];
    tabBar.hidden = !path;
    for (const b of tabBar.querySelectorAll('[data-dx-tab]')) b.setAttribute('aria-selected', String(b.dataset.dxTab === page));
    // the classic page's own tab of the same name (the overview is this page's)
    classic.href = path ? `/?sel=${encodeURIComponent(path)}${page && page !== 'overview' ? `&page=${page}` : ''}` : '/';
    drawCrumbs(path, e, page);
    keep.hidden = !path;
    let text, url = BOARDS;
    if (path) {
      // the entity's feeds start now; the panels' own bindings take them over (counted), and these let go after. 'peek' stays
      // while the entity is selected: its State and its settings may come after the selection (refresh, below)
      const kinds = ['checks', 'config', 'levels', 'actions'];
      for (const k of [...kinds, 'peek']) feed.use(k, path);
      const tpl = templates.get(tab.file) || await template(tab.file);
      templates.set(tab.file, tpl);
      const entity = await feed.whenReady(path);
      if (seq !== current.seq) { for (const k of [...kinds, 'peek']) feed.release(k, path); return; }
      text = tabText(tab, tpl, path, entity);
      const was = current.release;
      current.release = null;
      const opened = await openBase(detail, text, url);
      if (tab.id === 'log') {
        // the proposal drawn is the one the text was made in (a pick that came while the entity was loading is in it)
        dressLog(detail, logVariant(), { fresh: true });
        drawCrumbs(path, e, page);
      } else delete detail.dataset.logVariant;
      if (was) was();
      for (const k of kinds) feed.release(k, path);
      const hold = { panels: opened };
      current = { ...current, path, page, urlPage, tab, hold, sig: sigOf(entity), release: () => { hold.panels.release(); feed.release('peek', path); }, text };
    } else {
      // nothing picked: the Overview starter, without its own time bar (Explore's is the page's)
      text = withoutPlayback(await template('overview.base'));
      if (seq !== current.seq) return;
      const was = current.release;
      const opened = await openBase(detail, text, url);
      delete detail.dataset.logVariant;
      if (was) was();
      current = { ...current, path: null, page: '', urlPage: '', tab: null, hold: null, sig: '', release: () => opened.release(), text };
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
    if (!current.tab) return;
    const text = tabText(current.tab, templates.get(current.tab.file), current.path, entity);
    current.sig = sig;
    if (text === current.text) return;
    await redraw(text);
  }
  /** The same tab drawn again from new text, the reader's state kept (tabs, rails, t). */
  async function redraw(text) {
    refreshing = true;
    const { hold, seq } = current;
    try {
      const opened = await openBase(detail, text, BOARDS, { keepState: true });
      if (seq !== current.seq) { opened.release(); return; }
      if (current.tab && current.tab.id === 'log') dressLog(detail, logVariant(), { fresh: false });
      const old = hold.panels;
      hold.panels = opened;
      old.release();
      current.text = text;
      showTimeBar(!hasPlayback(parseBase(text)));
    } finally { refreshing = false; }
  }
  feed.addEventListener('values', refresh);
  feed.addEventListener('node', refresh);
  // the Log's breadcrumb scope (proposal 4): kept on this device, the Log drawn again with its tab and t
  crumbs.addEventListener('change', (e) => {
    const s = e.target.closest('[data-log-scope]');
    if (!s || !current.path || !current.tab || current.tab.id !== 'log') return;
    store.set(LOG_SCOPE, s.value === 'children' ? null : s.value);
    redraw(tabText(current.tab, templates.get(current.tab.file), current.path, feed.entity(current.path)));
  });
  // another Log proposal picked: the Log tab drawn in it now (its crumbs too), nothing else moves
  const onLogVariant = () => { if (current.path && current.tab && current.tab.id === 'log') select(current.path, current.urlPage); };
  addEventListener('dx-log-variant', onLogVariant);
  keep.addEventListener('click', async () => {
    if (!current.path || !window.__dxBoards) return;
    const e = feed.dataset('entities').latestAt(Infinity, current.path);
    try {
      const id = await window.__dxBoards.create({ title: `Explore ${(e && e.name) || current.path}`, text: detail.layoutText ? detail.layoutText() : current.text });
      go('dashboards', { board: id });
    } catch (err) { crumbs.textContent = `Not kept: ${err.message}`; }
  });
  await select(params.sel, params.page);
  return {
    mark(p) { tree.select(p.sel || null); markTable(p.sel || null); },
    update(p) { if (p.sel !== (current.path || '') || (p.sel && (p.page || '') !== (current.urlPage || ''))) return select(p.sel, p.page); return null; },
    unmount() { feed.removeEventListener('values', refresh); feed.removeEventListener('node', refresh); removeEventListener('dx-log-variant', onLogVariant); if (current.release) current.release(); current.seq += 1; tree.destroy(); if (sizer) sizer.destroy(); },
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
// WHY data-mode too: the classic tree's tokens (tokens.css, skins/shadcn.css) are chosen by <html data-mode>
const systemDark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
function applyTheme(value) {
  const html = document.documentElement;
  if (value === 'light' || value === 'dark') html.dataset.theme = value; else delete html.dataset.theme;
  html.dataset.mode = value === 'light' || value === 'dark' ? value : systemDark && systemDark.matches ? 'dark' : 'light';
}
if (systemDark) systemDark.addEventListener('change', () => applyTheme(store.get(THEME)));
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
  // WHY at once, before the queue: a destination may still be loading the last selection's panels (seconds, on a slow
  // entity); what the URL now says is marked as it changes (the tree's selected row), the panels follow in order
  const asked = readUrl();
  if (shown.dest === asked.go && shown.handle && shown.handle.mark) shown.handle.mark(asked);
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

// Add to dashboard (data-ui CONTRACT §34): the picker lists the host's dashboards, and the page saves the panel (add.js, PEP §17)
installAddToDashboard(app, { BOARDS, go, declaredFile });

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
  barPick.value = barVariant();
  drawTimeBar();
  timeBar.data = feed.dataset('summary');
  drawHistory();
  await render();
  const t = readT();
  if (t !== null) clock.seek(t, { source: 'url' });
})();
