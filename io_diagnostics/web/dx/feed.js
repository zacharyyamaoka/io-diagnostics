/* RobotDx's live feed: today's /api/v1 read into data-ui LiveDatasets (docs/peps/0001-robotdx-on-data-ui.md §12.1).

   Zach, 2026-10-26: "robotdx is basically yet again just like a dataset and viewer … One big difference here is that it
   is LIVE data … at any moment you can just kinda like 'rewind'". So the host stays as it is and this file turns what
   it serves into datasets that are still growing: data-ui draws them as of the clock's t, and rewinding is a query.

     shared, always fed   entities summary attention runs (SSE /live: a snapshot of /state, then only what changed; GET
                          /state on an older host), events faults (GET /activity, SSE /events)
     per watched entity   checks config (GET /node), levels (GET /history), samples (GET /history + /values for the backlog,
                          then /live's samples; /values polled while the feed lags the watch: an older host)
     the rack, on demand  checks of every entity (GET /checks) for a checks binding with no ?path=; the tree's past (GET
                          /history/tree, once, when the page opens)

   A watched entity (PEP §12.1) is one a view on screen needs: use(kind, path) / release(kind, path), counted; its
   polls start with the first use and stop 10 s after the last release. Nothing polls while the tab is hidden (the SSE
   stream stays open: it is push, one request). Every request is a GET: the one write, the watch heartbeat, is
   watch.js's and nobody else's.
   Time: every row's `t` is the host's clock (PEP §5c8), never the page's, so a replayed fixture reads 18:11 as it did on
   the rack and the time bar's ticks are the host's. */

import { LiveDataset } from '../vendor/data-ui/data-ui.js';

const API = '/api/v1';
const MARK = { warn: '▲ warn', unknown: '? unknown', stale: '◷ stale', error: '✖ error' };
const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
const KINDS = ['entities', 'summary', 'attention', 'runs', 'events', 'faults', 'checks', 'config', 'levels', 'samples'];
const PER_ENTITY = new Set(['levels', 'samples']);       // one dataset per entity
const WATCHED = new Set(['checks', 'config', 'levels', 'samples', 'peek']);   // a binding with ?path= makes the entity watched
// 'peek' reads an entity's /node and /values (what it has: its State keys, its settings) and is never State on screen: no heartbeat
const STATE_EVERY_MS = 2000;     // /state at most this often (a tick asks sooner than that only once)
const STATE_IDLE_MS = 5000;      // and this often with no tick at all
const NODE_EVERY_MS = 2000;
const HISTORY_EVERY_MS = 4000;
const VALUES_LIVE_MS = 250;      // while the watch runs (today's d6.js)
const VALUES_IDLE_MS = 1000;
const FAULTS_EVERY_MS = 4000;
const LINGER_MS = 10000;         // an entity's polls outlive its last view this long (a tab switch does not restart them)
const LIVE_FIRST_MS = 8000;      // /live must send its snapshot within this, or the page polls /state (an older host, a 404)
// with /live on, an entity's /node and /history are read when its tree row changes (a report arrived) and on a slow beat
const NODE_ON_CHANGE_MS = 1000, NODE_LIVE_EVERY_MS = 10000, HISTORY_ON_CHANGE_MS = 2000, HISTORY_LIVE_EVERY_MS = 15000;
// every check of the rack (GET /checks): as /node's, on a row change and a slow beat with /live, every 2 s on the /state poll
const CHECKS_EVERY_MS = 2000, CHECKS_ON_CHANGE_MS = 1000, CHECKS_LIVE_EVERY_MS = 10000;
// the feed's pace while a watch runs (PEP 0001 §15): CADENCE_RUN samples events in a row under CADENCE_GAP_MS apart, or
// FIRST_POLLS polls in a row the feed beat, and the /values poll beside the feed stops
const CADENCE_GAP_MS = 500, CADENCE_RUN = 3, FIRST_POLLS = 12;
const TREE_BIN_S = 2;            // the tree's past (GET /history/tree): bins of about this, over the host's window
// a past version of a tree row (from /history/tree) has today's identity and place, its level then, and nothing else
const PAST_BLANK = { message: '', below: '', presence: '', rate: '', heard: null, checks: null, actions: null, faults: null, knock_on: '' };

// WHY a shape and a word, nothing for OK (E9, Zach's rule): a level is never only a colour, and OK is quiet
export const health = (level) => MARK[level] || '';
export const rank = (level) => (level in RANK ? RANK[level] : -1);
const usb = (text) => (globalThis.IOUsb ? globalThis.IOUsb.label(text) : String(text ?? ''));
// UCUM as people read it (today's charts/core.js and d4.js map the same five)
const UNITS = { GiBy: 'GiB', MiBy: 'MiB', KiBy: 'KiB', By: 'B', Cel: '°C', 1: '', 'N.m': 'N·m' };
export const unitText = (u) => (u in UNITS ? UNITS[u] : String(u || ''));
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const hidden = () => typeof document !== 'undefined' && document.hidden;

/** A binding's kind and entity from a dataset URL: datasets/samples.json?path=host/… → {kind: 'samples', path}. */
export function bindingOf(url) {
  try {
    const u = new URL(url, location.href);
    const kind = (u.pathname.match(/\/dx\/datasets\/([a-z]+)\.json$/) || [])[1];
    if (!kind || !KINDS.includes(kind)) return null;
    return { kind, path: u.searchParams.get('path') || '' };
  } catch (_) { return null; }
}

function flatten(node, out = []) { out.push(node); for (const child of node.children || []) flatten(child, out); return out; }

/** One entity row of /state's tree, as the entities dataset holds it (the fields the page draws; no ages: they tick). */
function entityRow(n) {
  const id = n.identity || {};
  const hint = n.hint || null;
  const presence = n.presence || null;
  const rate = n.rate || null;
  return {
    path: n.path, parent: n.parent || null, name: usb(n.name), kind: n.kind, icon: n.icon, depth: n.depth,
    health: health(n.level), level: n.level ?? null, rank: rank(n.level), message: n.message || '',
    below: hint ? `${health(hint.level)} · ${hint.message || ''}`.replace(/^ · /, '') : '',
    model: id.model || '', serial: id.serial_number || '', firmware: id.sw_version || '', model_id: id.model_id || '',
    id_text: usb(n.id_text || ''), hardware_id: n.hardware_id || '',
    // WHY a word, not a colour (E9): presence is neutral; "streaming" while a follower is live
    presence: !presence ? '' : presence.state === 'live' ? 'streaming' : presence.state === 'stale' ? (presence.text || 'not heard lately') : 'heard',
    link: n.usb && globalThis.IOUsb ? globalThis.IOUsb.describe(n.usb) || '' : '',
    // WHY a quiet note, never a level (E12): "0.20 /s · expected 0.20"
    rate: rate && rate.expected_per_s ? `${rate.received_per_s === null || rate.received_per_s === undefined ? 'measuring' : `${rate.received_per_s.toFixed(2)} /s`} · expected ${rate.expected_per_s.toFixed(2)}` : '',
    heard: n.last_received ?? null, checks: n.checks || 0, actions: n.actions || 0, faults: n.faults || 0,
    knock_on: n.knock_on_of || '', connected: n.connected !== false, stale: !!n.stale, claimed: n.claimed !== false, owner: n.owner || '',
  };
}

/** One check as the checks dataset holds it (from /node's `checks` or /checks' lists: the same check_json). */
function checkRow(path, entity, c) {
  const level = c.effective_level || c.level;
  return { id: `${path}#${c.name}`, path, entity, name: c.name, health: health(level), level, said: c.level, rank: rank(level),
    message: c.message || '', code: c.code || '', received: c.received ?? null, period: c.expected_period_s ?? null, source: c.source || '',
    why: (c.values || []).filter((v) => v.key === 'what_to_check' || v.key === 'fault_meaning').map((v) => v.value).join(' · ') };
}

/** Upsert the checks that changed (a new report at its receive time; a change with no arrival, going stale, at the host's now)
    and remove those of `scope` that left. WHY one `seen` for every reader (a /node per entity, /checks for the rack): one check
    is then one row whoever read it, and reading it twice is not a second version. */
function diffChecks(seen, now, ds, nowHost, scope) {
  const up = [];
  for (const [k, r] of now) {
    const was = seen.get(k);
    if (!same(was, r)) { seen.set(k, r); up.push({ ...r, t: was && was.received === r.received ? nowHost : r.received ?? nowHost }); }
  }
  const gone = [...seen.keys()].filter((k) => scope(seen.get(k)) && !now.has(k));
  for (const k of gone) seen.delete(k);
  // the store wants each key's versions in time order; a version older than the key's newest goes in at its place
  if (up.length) ds.upsert(up.sort((a, b) => a.t - b.t));
  if (gone.length) ds.remove(gone, nowHost);
}

export class RobotDxFeed extends EventTarget {
  constructor({ clock, api = API, schemas = 'datasets/' } = {}) {
    super();
    this.clock = clock;
    this.api = api;
    this.schemaBase = new URL(schemas, import.meta.url).href;
    this.schemas = {};
    this._sets = new Map();          // 'entities' | 'samples:host/…' -> LiveDataset
    this._entities = new Map();      // path -> EntityFeed
    this._seen = new Map();          // entities: path -> row (to upsert only what changed)
    this._seenSummary = null;
    this._seenAttention = new Map();
    this._seenRuns = new Map();
    this._seenFaults = new Map();
    this._seenEvents = new Set();
    this._stats = {};
    this._state = { at: 0, inFlight: false, wanted: false, tries: 0 };
    this._faultsAt = 0;
    this._faultUses = 0;
    this.host = { now: null, offset: 0, fixture: null, window: 900, ok: false };
    this._timer = null;
    this.mode = null;                // 'live' (GET /api/v1/live) or 'poll' (GET /state; an older host)
    this._mirror = null;             // /live: {tree: Map(path -> node with child paths), state: {section: value}}
    this._anchor = null;             // {now: the host's seconds, at: performance.now()} (time is not diffed: PEP §14)
    this._liveQueue = [];            // /live events that came before the datasets existed
    this._liveReady = false;
    this._flushTimer = null;
    this._touched = new Set();
    this.openedAt = null;            // the host's time of the first /state the page applied: the page opened then
    this.treeBack = null;            // the tree's past: {supported, from, bin_s} once GET /history/tree answered
    this._seenChecks = new Map();    // checks: id -> the row last written, shared by every reader (diffChecks)
    this._allChecks = { uses: 0, released: 0, at: 0, busy: false, due: true, supported: null };   // GET /checks (no ?path=)
    this._onVisible = () => { if (!hidden()) { this._state.wanted = true; this._kick(); } };
    this.addEventListener('watch', (e) => { const ent = this._entities.get(e.detail.path); if (ent) ent.watch = e.detail.watch; });
  }

  /** What the page knows of an entity now: {node, keys: Map(key -> {unit, meaning, group}), watchSupported, ready}. */
  entity(path) {
    const e = this._entities.get(path);
    return e ? { node: e.node || null, keys: e.keys, watchSupported: e.watchSupported, ready: e.nodeOk && (!e.watchSupported || e.valuesOk), feedKeepsUp: e.feedKeepsUp } : null;
  }

  /** Resolves once the entity's first /node (and, when it has State, its first /values) answered; at most `ms`. */
  whenReady(path, ms = 2500) {
    return new Promise((resolve) => {
      const done = () => { clearTimeout(timer); this.removeEventListener('node', on); this.removeEventListener('values', on); resolve(this.entity(path)); };
      const on = () => { const e = this.entity(path); if (e && e.ready) done(); };
      const timer = setTimeout(done, ms);
      this.addEventListener('node', on);
      this.addEventListener('values', on);
      on();
    });
  }

  // ---------------------------------------------------------------- datasets

  /** The page's LiveDataset for a kind (and entity, for levels / samples): made on first ask, bound to the clock. */
  dataset(kind, path = '') {
    if (!KINDS.includes(kind)) return null;
    const name = PER_ENTITY.has(kind) ? `${kind}:${path}` : kind;
    let ds = this._sets.get(name);
    if (ds) return ds;
    const schema = this.schemas[kind] || { properties: {} };
    const spec = { entities: { key: 'path' }, summary: { key: 'host' }, attention: { key: 'path' }, runs: { key: 'id' },
      faults: { key: 'id' }, checks: { key: 'id' }, config: { key: 'id' }, events: {}, levels: { stream: 'check' },
      samples: { stream: 'key' } }[kind];
    const age = this.host.window || 900;
    ds = new LiveDataset({
      id: name, title: schema.title || kind, titleProperty: schema.titleProperty, properties: schema.properties, time: 't',
      ...spec,
      // WHY the host's window: the host keeps levels and measurements that long (§5c4), so rewind covers the same span
      retention: kind === 'samples' || kind === 'levels' || kind === 'events' || kind === 'summary' ? { age } : {},
      // WHY 0: staleness is the host's call (PEP §5a8); the page says reconnecting itself when the host goes away
      stale: 0, url: new URL(`${kind}.json${path ? `?path=${encodeURIComponent(path)}` : ''}`, this.schemaBase).href,
    });
    ds.setStatus(this.host.ok ? 'live' : 'connecting', this._detail());
    this._sets.set(name, ds);
    if (this.clock) this.clock.bind(ds);
    return ds;
  }

  /** Every dataset the page holds now: {name: LiveDataset}. */
  datasets() { return Object.fromEntries(this._sets); }

  /** A view needs this kind of data (of this entity): start (or keep) its polls. */
  use(kind, path) {
    if (kind === 'faults') { this._faultUses += 1; this._kick(); return; }
    // a checks binding with no ?path= is every entity's checks: one GET /checks for the rack (PEP 0001 §15)
    if (kind === 'checks' && !path) { this._allChecks.uses += 1; this._allChecks.due = true; this._kick(); return; }
    if (!WATCHED.has(kind) || !path) return;
    let e = this._entities.get(path);
    if (!e) { e = new EntityFeed(this, path); this._entities.set(path, e); }
    e.use(kind);
    this._kick();
  }

  release(kind, path) {
    if (kind === 'faults') { this._faultUses = Math.max(0, this._faultUses - 1); return; }
    if (kind === 'checks' && !path) { const a = this._allChecks; a.uses = Math.max(0, a.uses - 1); if (!a.uses) a.released = Date.now(); return; }
    const e = this._entities.get(path);
    if (e) e.release(kind);
  }

  /** Entity paths whose State is on screen (watch.js sends their heartbeat). */
  watchedState() {
    return [...this._entities.values()].filter((e) => e.uses.samples > 0 && e.watchSupported).map((e) => e.path);
  }

  /** {endpoint: {requests, bytes}} since the page opened (the page's own KPI K2 count). */
  stats() { return JSON.parse(JSON.stringify(this._stats)); }

  // ---------------------------------------------------------------- start / stop

  async start() {
    // WHY all at once: the tree is drawn from the first snapshot, so nothing it does not need waits in front of it (K7)
    const schemas = Promise.all(KINDS.map(async (kind) => {
      try { this.schemas[kind] = await (await fetch(new URL(`${kind}.json`, this.schemaBase))).json(); } catch (_) { this.schemas[kind] = { properties: {} }; }
    }));
    const settings = this._get('/settings').catch(() => null);
    const live = this._openLive();
    this._state.inFlight = true;
    await schemas;
    const s = await settings;
    if (s && s.history_window_s) this.host.window = s.history_window_s;
    for (const kind of ['entities', 'summary', 'attention', 'runs', 'events', 'faults', 'checks', 'config']) this.dataset(kind);
    this._liveReady = true;
    for (const [type, data] of this._liveQueue.splice(0)) this._liveEvent(type, data);
    if ((await live) === 'live') this.mode = 'live';
    else {
      // an older host (no /api/v1/live: 404) or a feed that never sent its snapshot: phase 3's /state poll
      this.mode = 'poll';
      let st = null;
      try { st = await this._get('/state'); } catch (_) { /* reconnecting */ }
      this._applyState(st);
    }
    // the event backlog, then the stream (an SSE event older than the backlog would otherwise go in first)
    this._readActivity(true).then(() => this._connectEvents());
    document.addEventListener('visibilitychange', this._onVisible);
    this._loop();
  }

  stop() {
    clearTimeout(this._timer);
    if (this._es) this._es.close();
    if (this._live) this._live.close();
    document.removeEventListener('visibilitychange', this._onVisible);
  }

  _detail() {
    const f = this.host.fixture;
    // WHY the fixture said in the connection line (E1): "a replay says recorded", never a live line that hides it
    return f ? `replay of ${f.name} (recorded)` : '';
  }

  _status(state) {
    for (const ds of this._sets.values()) ds.setStatus(state, state === 'reconnecting' ? '' : this._detail(), state === 'reconnecting' ? this._state.tries : 0);
  }

  async _get(endpoint) {
    const res = await fetch(`${this.api}${endpoint}`, { cache: 'no-store' });
    const text = await res.text();
    const key = endpoint.split('?')[0];
    const s = this._stats[key] || (this._stats[key] = { requests: 0, bytes: 0 });
    s.requests += 1;
    s.bytes += text.length;
    if (!res.ok) { const err = new Error(`${res.status} ${key}`); err.status = res.status; throw err; }
    return JSON.parse(text);
  }

  /** The one scheduler: what is due now, never two of the same request at once, nothing while hidden. */
  _loop() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._loop(), 100);
    if (hidden()) return;
    const now = Date.now();
    const st = this._state;
    if (this.mode === 'poll' && !st.inFlight && ((st.wanted && now - st.at >= STATE_EVERY_MS) || now - st.at >= STATE_IDLE_MS)) this._readState();
    if (this._faultUses > 0 && now - this._faultsAt >= FAULTS_EVERY_MS) this._readActivity(false);
    const a = this._allChecks;
    if ((a.uses > 0 || now - a.released <= LINGER_MS) && !a.busy && a.supported !== false) {
      const live = this.mode === 'live';
      if (!a.at || (live ? (a.due && now - a.at >= CHECKS_ON_CHANGE_MS) || now - a.at >= CHECKS_LIVE_EVERY_MS : now - a.at >= CHECKS_EVERY_MS)) { a.due = false; this._readChecks(); }
    }
    for (const e of this._entities.values()) e.tick(now);
  }

  _kick() { if (!hidden()) this._loop(); }

  // ---------------------------------------------------------------- GET /api/v1/live: /state as a change-only stream

  /** The host's clock now: the snapshot's (or a samples event's) `now`, advanced by the page's own clock since. */
  hostNow() { return this._anchor ? this._anchor.now + (performance.now() - this._anchor.at) / 1000 : this.host.now; }

  /** Open the feed; resolves 'live' at its first snapshot, 'poll' when it closes before one (a 404) or never sends one. */
  _openLive() {
    return new Promise((resolve) => {
      if (typeof EventSource === 'undefined') { resolve('poll'); return; }
      const es = new EventSource(`${this.api}/live`);
      this._live = es;
      const s = this._stats['/live'] || (this._stats['/live'] = { requests: 0, bytes: 0 });
      s.requests += 1;
      let settled = false;
      const settle = (how) => { if (settled) return; settled = true; clearTimeout(timer); if (how === 'poll') es.close(); resolve(how); };
      const timer = setTimeout(() => settle('poll'), LIVE_FIRST_MS);
      for (const type of ['snapshot', 'upsert', 'remove', 'samples']) {
        es.addEventListener(type, (m) => {
          s.bytes += m.data.length;
          let data;
          try { data = JSON.parse(m.data); } catch (_) { return; }
          if (this._liveReady) this._liveEvent(type, data); else this._liveQueue.push([type, data]);
          if (type === 'snapshot') settle('live');
        });
      }
      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {
          // closed for good: before a snapshot it is an older host (a 404); after one, the host is gone or lost the feed
          if (!settled) settle('poll');
          else if (this.mode === 'live') { this.mode = 'poll'; this._state.wanted = true; this._kick(); }
        } else if (settled && this.mode === 'live') {
          // the browser retries with Last-Event-ID; the host replays what was missed, or sends a fresh snapshot
          this._state.tries += 1;
          this.host.ok = false;
          this._status('reconnecting');
        }
      };
    });
  }

  _liveEvent(type, data) {
    if (type === 'snapshot') {
      // a snapshot replaces everything the page holds (a first connect, or a resume the host could not serve)
      this._mirror = { tree: new Map(), state: {} };
      // WHY not `emitted` (5cdb4b9): it is the event's own stamp, beside /state's keys in a snapshot, not a state section
      for (const [k, v] of Object.entries(data)) if (k !== 'tree' && k !== 'emitted') this._mirror.state[k] = v;
      // a snapshot may be another host (a restart, an upgrade): what the page learned of the feed's pace starts again
      for (const e of this._entities.values()) e.forgetPace();
      const walk = (n) => { this._mirror.tree.set(n.path, { ...n, children: (n.children || []).map((c) => c.path) }); for (const c of n.children || []) walk(c); };
      if (data.tree) walk(data.tree);
      if (data.clock && Number.isFinite(data.clock.now)) this._anchor = { now: data.clock.now, at: performance.now() };
      this._applyState(data);
      return;
    }
    if (!this._mirror) return;
    if (type === 'upsert') {
      if (data.table === 'tree') { this._mirror.tree.set(data.id, data.row); this._touched.add(data.id); } else this._mirror.state[data.id] = data.row;
      // a clock section sent for another reason carries the host's now: anchor to it
      if (data.table === 'state' && data.id === 'clock' && data.row && Number.isFinite(data.row.now)) this._anchor = { now: data.row.now, at: performance.now() };
      this._flushSoon();
    } else if (type === 'remove') {
      if (data.table === 'tree') this._mirror.tree.delete(data.id); else delete this._mirror.state[data.id];
      this._flushSoon();
    } else if (type === 'samples') {
      if (Number.isFinite(data.now)) this._anchor = { now: data.now, at: performance.now() };
      const e = this._entities.get(data.path);
      if (e) e.ingest(data);
    }
  }

  /** One tick's events are one apply (the host sends them together): the mirror back into /state's shape, diffed by row. */
  _flushSoon() {
    if (this._flushTimer) return;
    this._flushTimer = setTimeout(() => {
      this._flushTimer = null;
      const m = this._mirror;
      if (!m) return;
      const root = [...m.tree.values()].find((n) => !n.parent) || m.tree.get('host');
      const nest = (n, seen = new Set()) => {
        if (!n || seen.has(n.path)) return null;
        seen.add(n.path);
        return { ...n, children: (n.children || []).map((p) => nest(m.tree.get(p), seen)).filter(Boolean) };
      };
      const clock = { ...(m.state.clock || {}), now: this.hostNow() };
      this._applyState({ ...m.state, clock, tree: root ? nest(root) : { path: 'host', children: [] } });
      for (const path of this._touched) { const e = this._entities.get(path); if (e) e.rowChanged(); }
      if (this._touched.size) this._allChecks.due = true;
      this._touched.clear();
    }, 0);
  }

  // ---------------------------------------------------------------- /state: the tree, the summary, what needs you, runs

  async _readState() {
    this._state.inFlight = true;
    this._state.wanted = false;   // a tick that comes while this request is out asks again
    let s = null;
    try { s = await this._get('/state'); } catch (_) { /* below */ }
    this._applyState(s);
  }

  _applyState(s) {
    const st = this._state;
    st.inFlight = false;
    st.at = Date.now();
    if (!s) {
      st.tries += 1;
      this.host.ok = false;
      this._status('reconnecting');
      return;
    }
    if (st.tries || !this.host.ok) { st.tries = 0; this.host.ok = true; this.host.fixture = s.fixture || null; this._status('live'); }
    const t = s.clock.now;
    const first = this.openedAt === null;
    if (first) this.openedAt = t;
    this.host.now = t;
    this.host.offset = t - Date.now() / 1000;
    this.host.rank = s.clock.rank;
    // entities: a version only for a row that changed; a row that left is removed at t
    const rows = flatten(s.tree).map(entityRow);
    const changed = [];
    const here = new Set();
    for (const r of rows) {
      here.add(r.path);
      if (!same(this._seen.get(r.path), r)) { this._seen.set(r.path, r); changed.push({ ...r, t }); }
    }
    const gone = [...this._seen.keys()].filter((p) => !here.has(p));
    for (const p of gone) this._seen.delete(p);
    const entities = this.dataset('entities');
    if (changed.length) entities.upsert(changed);
    if (gone.length) entities.remove(gone, t);
    // the summary (the chip's question), one row
    const sum = s.summary || {};
    const counts = sum.counts || {};
    const srow = { host: 'host', level: sum.level ?? null, health: health(sum.level), needs: (sum.attention || []).length,
      errors: counts.error || 0, warns: counts.warn || 0, unknowns: counts.unknown || 0, stales: counts.stale || 0,
      devices: sum.devices || 0, entities: sum.entities || rows.length, chip: (s.chip && s.chip.label) || '' };
    // WHY a version on every answer, changed or not: the connection line's "last data N s ago" is then the last time the
    // host answered (today's status bar, E8), not the last time something changed
    this._seenSummary = srow;
    this.dataset('summary').upsert([{ ...srow, t }]);
    // what needs you, worst first (the host counts: one cause counted once, E2)
    const att = new Map((sum.attention || []).map((a) => [a.path, { path: a.path, name: usb(a.name), device: a.device ? usb(a.device.name) : '',
      level: a.level, health: health(a.level), rank: rank(a.level), message: a.message || '' }]));
    this._diff(this._seenAttention, att, this.dataset('attention'), t);
    const runs = new Map(((s.runs && s.runs.items) || []).map((r) => [r.id, { id: r.id, label: r.label || r.action || r.id, state: r.state || '',
      active: !!r.active, started: r.started ?? null, elapsed: r.elapsed_s ?? null, exit_code: r.exit_code ?? null }]));
    this._diff(this._seenRuns, runs, this.dataset('runs'), t);
    this.dispatchEvent(new CustomEvent('state', { detail: { t, fixture: this.host.fixture } }));
    if (first) this._treeBack();
  }

  /** The tree's past (GET /history/tree, PEP 0001 §15), read once when the page opens: each row's health bar becomes versions of
      today's row before that moment, a version at a bin's start whenever its level changes. WHY today's row with only the level:
      the host keeps each row's statuses over its window, not the row's name, place, message or counts then; so the past tree is
      the rows of now, each with the worst level it held in each bin, and the rest of the row is empty (the note says so). */
  async _treeBack() {
    const bins = Math.min(1000, Math.max(1, Math.ceil((this.host.window || 900) / TREE_BIN_S)));
    let h;
    try { h = await this._get(`/history/tree?bins=${bins}`); } catch (err) {
      // the host away for a moment as the page opened: once more, 5 s later
      if (!(err && err.status) && !this._treeRetried) { this._treeRetried = true; setTimeout(() => this._treeBack(), 5000); return; }
      // a host before 63019fb (a 404): the tree before the page opened stays empty, and the note says why
      this.treeBack = { supported: false };
      this.dispatchEvent(new CustomEvent('tree-back', { detail: this.treeBack }));
      return;
    }
    const rows = h.rows || [];
    // the host's history starts at the first bin any row has a level in: before it the host knew nothing, and nothing is drawn
    const starts = rows.map((r) => (r.bar || []).findIndex((x) => x !== null && x !== undefined)).filter((i) => i >= 0);
    const first = starts.length ? Math.min(...starts) : -1;
    const versions = [];
    for (const row of first < 0 ? [] : rows) {
      const today = this._seen.get(row.path);
      if (!today) continue;   // a row the page does not hold (it left as the page opened)
      let last;
      for (let i = first; i < row.bar.length; i += 1) {
        const t = h.from + i * h.bin_s;
        if (t >= this.openedAt) break;
        const level = row.bar[i] ?? null;
        if (i > first && level === last) continue;
        last = level;
        versions.push({ ...today, ...PAST_BLANK, health: health(level), level, rank: rank(level), stale: level === 'stale', t });
      }
    }
    if (versions.length) this.dataset('entities').upsert(versions.sort((a, b) => a.t - b.t));
    this.treeBack = { supported: true, from: first < 0 ? null : h.from + first * h.bin_s, bin_s: h.bin_s, versions: versions.length };
    this.dispatchEvent(new CustomEvent('tree-back', { detail: this.treeBack }));
  }

  /** Every check of the rack in one answer (GET /checks, PEP 0001 §15), for a checks binding with no ?path=. */
  async _readChecks() {
    const a = this._allChecks;
    a.busy = true;
    a.at = Date.now();
    let answer;
    try { answer = await this._get('/checks'); } catch (err) {
      a.busy = false;
      // WHY no fallback of N /node reads: that is what /checks replaces. On a host before 63019fb the checks of the entities the
      // page watches are what the rack's view shows (their /node fills the same dataset)
      if (err && err.status === 404) a.supported = false;
      return;
    }
    a.busy = false;
    a.supported = true;
    const nowHost = answer.clock && Number.isFinite(answer.clock.now) ? answer.clock.now : this.hostNow();
    const rows = new Map();
    for (const [path, list] of Object.entries(answer.checks || {})) {
      const entity = (this._seen.get(path) || {}).name || usb(path);
      for (const c of list || []) { const r = checkRow(path, entity, c); rows.set(r.id, r); }
    }
    diffChecks(this._seenChecks, rows, this.dataset('checks'), nowHost, () => true);
    this.dispatchEvent(new CustomEvent('checks', { detail: { at: nowHost, checks: rows.size } }));
  }

  /** Upsert what changed in `now` against `seen`, remove what left. */
  _diff(seen, now, ds, t) {
    const up = [];
    for (const [k, r] of now) if (!same(seen.get(k), r)) { seen.set(k, r); up.push({ ...r, t }); }
    const gone = [...seen.keys()].filter((k) => !now.has(k));
    for (const k of gone) seen.delete(k);
    if (up.length) ds.upsert(up);
    if (gone.length) ds.remove(gone, t);
  }

  // ---------------------------------------------------------------- the event log and the faults

  async _readActivity(first) {
    this._faultsAt = Date.now();
    let a;
    try { a = await this._get(`/activity?limit=${first ? 500 : 1}`); } catch (_) { return; }
    const t = (a.clock && a.clock.now) || this.host.now;
    if (first) {
      // the backlog inside the host's window, oldest first (rows must arrive in time order)
      const from = t - (this.host.window || 900);
      const rows = (a.events || []).filter((e) => !e.provisional && (e.received ?? e.stamp) >= from).reverse().map((e) => this._eventRow(e)).filter(Boolean);
      if (rows.length) this.dataset('events').append(rows);
    }
    const faults = new Map((a.faults || []).map((f) => {
      const id = `${f.path}#${f.code}`;
      return [id, { id, path: f.path, name: usb(f.name || ''), code: f.code, severity: f.severity, health: health(f.severity === 'error' ? 'error' : 'warn'),
        status: f.status, active: !!f.active, count: f.occurrence_count || 0, first: f.first_occurred ?? null, last: f.last_occurred ?? null, message: f.message || '' }];
    }));
    this._diff(this._seenFaults, faults, this.dataset('faults'), t);
  }

  /** An event as a row; null for one already held. WHY no provisional rows: the host relabels a provisional plug in
      place once the kernel confirms it (the same id), and an append dataset cannot replace a row, so the log takes the
      confirmed one only and one fact stays one row (E31); the tree still shows the plug at once (a udev tick). */
  _eventRow(e) {
    if (!e || e.provisional || this._seenEvents.has(e.id)) return null;
    this._seenEvents.add(e.id);
    return { t: e.received ?? e.stamp, id: e.id, time: e.time || '', severity: e.severity || 'info', type: e.type || '', code: e.code || '',
      kind: e.kind || '', message: e.message || '', path: e.path || '', name: usb(e.name || ''), port: e.port || '', source: e.source || '', count: e.count || 1 };
  }

  _connectEvents() {
    if (typeof EventSource === 'undefined') return;
    const es = new EventSource(`${this.api}/events`);
    this._es = es;
    const s = this._stats['/events'] || (this._stats['/events'] = { requests: 0, bytes: 0 });
    s.requests += 1;
    es.addEventListener('activity', (m) => {
      s.bytes += m.data.length;
      try { const row = this._eventRow(JSON.parse(m.data)); if (row) this.dataset('events').append([row]); } catch (_) { /* a malformed line */ }
    });
    es.addEventListener('tick', (m) => {
      s.bytes += m.data.length;
      // WHY on a tick and not a 1 s timer: today's 1 s full-tree poll is 82 % of the page's bytes (PEP §8)
      this._state.wanted = true;
    });
    es.addEventListener('hello', (m) => { s.bytes += m.data.length; });
    es.onerror = () => { if (es.readyState === EventSource.CONNECTING) this._state.wanted = true; };
  }
}

/** The polls of one watched entity: /node (checks, config), /history (levels, the State backlog), /values (State). */
class EntityFeed {
  constructor(feed, path) {
    this.feed = feed;
    this.path = path;
    this.uses = { checks: 0, config: 0, levels: 0, samples: 0, peek: 0 };
    this.released = { checks: 0, config: 0, levels: 0, samples: 0, peek: 0 };   // when each kind's last view left
    this.nodeAt = 0;
    this.historyAt = 0;
    this.valuesAt = 0;
    this.busy = { node: false, history: false, values: false };
    this.watchSupported = false;
    this.watch = null;
    this.cursor = null;          // /values' serial cursor; null until the backlog is in
    this.seenConfig = new Map();
    this.lastLevel = new Map();  // check -> {from, level}
    this.lastPoint = new Map();  // key -> t of the newest point appended
    this.nodeOk = false;
    this.valuesOk = false;
    this.keys = new Map();       // State key -> {unit, meaning, group}
    this.keyOrder = new Map();   // State key -> its place in the host's list
    this.forgetPace();
  }

  /** What the page knows of the feed's pace for this entity (PEP 0001 §15); a new snapshot starts it again. */
  forgetPace() {
    this.feedKeepsUp = false;    // the feed brings this entity's State at the watch's rate: no /values poll beside it
    this.paceAt = null;          // the last samples event's stamp while the watch runs (emitted, else the page's clock)
    this.paceRun = 0;            // samples events in a row under CADENCE_GAP_MS after the one before
    this.idlePolls = 0;          // polls in a row that brought no point the feed had not already brought
    this.feedFirst = 0;          // samples events that brought points first since the last poll that did
  }

  _running() { return Boolean(this.watch && this.watch.state === 'running'); }

  use(kind) { this.uses[kind] += 1; }
  release(kind) { this.uses[kind] = Math.max(0, this.uses[kind] - 1); if (!this.uses[kind]) this.released[kind] = Date.now(); }
  _want(k, now = Date.now()) { return this.uses[k] > 0 || now - this.released[k] <= LINGER_MS; }

  /** /live upserted this entity's tree row: a report arrived, so its checks, settings and levels are read again soon. */
  rowChanged() { this.nodeDue = true; this.historyDue = true; }

  tick(now) {
    // WHY linger: Explore's detail is rebuilt on a selection and a dashboard on a reload; its polls must not restart
    const want = (k) => this._want(k, now);
    if (!['checks', 'config', 'levels', 'samples', 'peek'].some(want)) return;
    const live = this.feed.mode === 'live';
    // WHY on a change with /live: a row is upserted when a report arrives, so a rig that reports every 30 s is read every 30 s,
    // not every 2 s; the slow beat catches what changes with no arrival (a check going stale)
    const nodeDue = !this.nodeOk || (live ? (this.nodeDue && now - this.nodeAt >= NODE_ON_CHANGE_MS) || now - this.nodeAt >= NODE_LIVE_EVERY_MS : now - this.nodeAt >= NODE_EVERY_MS);
    if (!this.busy.node && nodeDue) { this.nodeDue = false; this._node(); }
    if (!this.nodeOk) return;
    // the host's level history: the health strips (levels), and once, the checks' own past (so Diagnostics rewinds too)
    const historyDue = !this.historyAt || (live ? (this.historyDue && now - this.historyAt >= HISTORY_ON_CHANGE_MS) || now - this.historyAt >= HISTORY_LIVE_EVERY_MS : now - this.historyAt >= HISTORY_EVERY_MS);
    if ((want('levels') || (want('checks') && !this.checksBack)) && !this.busy.history && historyDue) { this.historyDue = false; this._history(); }
    if ((want('samples') || want('peek')) && this.watchSupported && !this.busy.values) {
      // with /live, the backlog is read here and what comes after arrives as the feed's samples (ingest).
      // WHY a poll beside the feed while the watch runs, until the feed shows the watch's pace: a host before 5cc429f sends a
      // watched entity's samples only with its ~1-2 s change tick, and State would move in steps of a second (PEP 0001 §15)
      const running = this._running();
      const poll = !live || (running && !this.feedKeepsUp);
      if (this.cursor === null || (poll && now - this.valuesAt >= (running ? VALUES_LIVE_MS : VALUES_IDLE_MS))) this._values();
    }
  }

  /** /live's samples for this entity (the /values?since= shape): appended once its backlog is in, while a view wants them. */
  ingest(v) {
    if (this.cursor === null || !(this._want('samples') || this._want('peek'))) return;
    if (v.watch) this.watch = v.watch;
    this.cursor = Math.max(this.cursor, v.cursor || 0);
    const brought = this._appendValues(v, null);
    // the cadence rule: CADENCE_RUN events in a row, each under CADENCE_GAP_MS after the one before, while the watch runs
    if (this.feedKeepsUp || !this._running()) { this.paceAt = null; this.paceRun = 0; return; }
    const at = Number.isFinite(v.emitted) ? v.emitted : performance.now();
    this.paceRun = this.paceAt !== null && at - this.paceAt < CADENCE_GAP_MS ? this.paceRun + 1 : 0;
    this.paceAt = at;
    if (brought > 0) this.feedFirst += 1;
    if (this.paceRun >= CADENCE_RUN) this.feedKeepsUp = true;
  }

  async _node() {
    this.busy.node = true;
    this.nodeAt = Date.now();
    let n;
    try { n = await this.feed._get(`/node?path=${encodeURIComponent(this.path)}`); } catch (_) { this.busy.node = false; return; }
    this.busy.node = false;
    this.nodeOk = true;
    this.watchSupported = !!(n.watch && n.watch.supported);
    this.watch = n.watch || null;
    this.node = n;
    const name = usb((n.node && n.node.name) || this.path);
    const checks = new Map((n.checks || []).map((c) => { const r = checkRow(this.path, name, c); return [r.id, r]; }));
    const nowHost = (n.heard && n.heard.received !== null && n.heard.age_s !== null) ? n.heard.received + n.heard.age_s : this.feed.host.now;
    // the rack's /checks may hold the same checks: one map of what was written, this entity's part of it
    diffChecks(this.feed._seenChecks, checks, this.feed.dataset('checks'), nowHost, (r) => r && r.path === this.path);
    const config = new Map();
    for (const c of n.config || []) {
      const id = `${this.path}#${c.key}`;
      config.set(id, { id, path: this.path, key: c.key, value: c.value, unit: c.unit || '', expected: c.expected ?? null,
        differs: !!c.differs, source: c.expected_source || '', stored: c.stored ?? null, unsaved: !!c.unsaved, writable: !!c.writable, stamp: c.stamp ?? null });
    }
    this._diff(this.seenConfig, config, this.feed.dataset('config'), (r) => r.stamp ?? nowHost, nowHost);
    this.feed.dispatchEvent(new CustomEvent('node', { detail: { path: this.path, node: n } }));
  }

  _diff(seen, now, ds, timeOf, removeAt) {
    const up = [];
    for (const [k, r] of now) {
      const was = seen.get(k);
      if (!same(was, r)) { seen.set(k, r); up.push({ ...r, t: timeOf(r, was) }); }
    }
    const gone = [...seen.keys()].filter((k) => !now.has(k));
    for (const k of gone) seen.delete(k);
    // the store wants each key's versions in time order; a version older than the key's newest goes in at its place
    if (up.length) ds.upsert(up.sort((a, b) => a.t - b.t));
    if (gone.length) ds.remove(gone, removeAt);
  }

  async _history() {
    this.busy.history = true;
    this.historyAt = Date.now();
    const first = !this.lastLevel.size;
    let h;
    try { h = await this.feed._get(`/history?path=${encodeURIComponent(this.path)}${first ? '' : '&window=60s'}`); } catch (_) { this.busy.history = false; return; }
    this.busy.history = false;
    if (!this.checksBack) this._checksBack(h);
    if (!(this.uses.levels > 0 || Date.now() - this.released.levels <= LINGER_MS)) return;
    const ds = this.feed.dataset('levels', this.path);
    const rows = [];
    for (const s of h.statuses || []) {
      if (s.state) continue;   // a State series carries no levels (never in a health bar)
      for (const span of s.levels || []) {
        const last = this.lastLevel.get(s.name);
        // a row per change of level: the window's first span repeats the level in force, and is not a change
        if (last && (span.from <= last.from || span.level === last.level)) continue;
        this.lastLevel.set(s.name, { from: span.from, level: span.level });
        rows.push({ t: span.from, check: s.name, level: span.level ?? null, health: health(span.level), code: span.code || '', message: span.message || '' });
      }
    }
    if (rows.length) ds.append(rows.sort((a, b) => a.t - b.t));
    this._history0 = this._history0 || h;
  }

  /** The checks' past from the host's level spans (its 15 min window): a version per span at its start, the no-data
      spans as removals. WHY: the host keeps each check's level and message per span (PEP §5c4), so Diagnostics can show
      a moment before this page opened; what_to_check and the receive times are not kept, so those cells are empty then.
      From each check's newest report on, /node's own version (which has them) is the one shown. */
  _checksBack(h) {
    this.checksBack = true;
    const name = usb((this.node && this.node.node && this.node.node.name) || this.path);
    const versions = [], removals = [];
    for (const s of h.statuses || []) {
      if (s.state) continue;
      const id = `${this.path}#${s.name}`;
      for (const span of s.levels || []) {
        if (span.level === null || span.level === undefined) { removals.push([id, span.from]); continue; }
        versions.push({ id, path: this.path, entity: name, name: s.name, health: health(span.level), level: span.level, said: span.level, rank: rank(span.level),
          message: span.message || '', code: span.code || '', received: null, period: s.expected_period_s ?? null, source: '', why: '', t: span.from });
      }
    }
    const checks = this.feed.dataset('checks');
    if (versions.length) checks.upsert(versions.sort((a, b) => a.t - b.t));
    for (const [id, t] of removals.sort((a, b) => a[1] - b[1])) checks.remove([id], t);
  }

  async _values() {
    this.busy.values = true;
    this.valuesAt = Date.now();
    const backlog = this.cursor === null;
    let v, h = null;
    try {
      v = await this.feed._get(`/values?path=${encodeURIComponent(this.path)}&since=${backlog ? 0 : this.cursor}`);
      // the State backlog older than the ring: the host's 1 Hz history of the same values (PEP §9 decision 4)
      if (backlog) h = this._history0 || await this.feed._get(`/history?path=${encodeURIComponent(this.path)}`);
    } catch (_) { this.busy.values = false; return; }
    this.busy.values = false;
    if (!backlog && v.cursor < this.cursor) this.lastPoint.clear();   // the host restarted: its serial numbers began again
    this.cursor = v.cursor;
    if (v.watch) this.watch = v.watch;
    const brought = this._appendValues(v, h);
    // the "who comes first" rule, for a watch too slow for the cadence rule: FIRST_POLLS polls in a row that brought nothing the
    // feed had not, while the feed brought points first, and the poll stops
    if (backlog || this.feed.mode !== 'live' || !this._running() || this.feedKeepsUp) return;
    if (brought > 0) { this.idlePolls = 0; this.feedFirst = 0; return; }
    this.idlePolls += 1;
    if (this.idlePolls >= FIRST_POLLS && this.feedFirst >= 2) this.feedKeepsUp = true;
  }

  /** Points of a /values answer (the backlog with `h`, the host's 1 Hz history before its ring) into the samples dataset. */
  _appendValues(v, h) {
    const ds = this.feed.dataset('samples', this.path);
    const byKey = new Map();
    const unitOf = new Map();
    for (const g of v.groups || []) {
      for (const r of g.rows || []) {
        unitOf.set(r.key, { unit: unitText(r.unit), meaning: r.meaning || '', group: g.name || '' });
        const list = byKey.get(r.key) || [];
        for (const [t, value] of r.points || []) list.push([t, value]);
        byKey.set(r.key, list);
      }
    }
    if (h) {
      for (const s of h.statuses || []) {
        if (!s.state) continue;
        for (const m of s.measurements || []) {
          const ring = byKey.get(m.key);
          const firstRing = ring && ring.length ? Math.min(...ring.map((p) => p[0])) : Infinity;
          const older = (m.points || []).filter(([t]) => t < firstRing);
          if (!older.length) continue;
          if (!unitOf.has(m.key)) unitOf.set(m.key, { unit: unitText(m.unit), meaning: '', group: s.name || '' });
          byKey.set(m.key, [...older, ...(ring || [])]);
        }
      }
    }
    // WHY a fixed order per key (the host's own list, first seen first): the State table sorts by it; the State cards
    // keep the same order because one sample of every key arrives together (one time) and their window is not capped
    for (const key of [...(v.groups || []).flatMap((g) => (g.rows || []).map((r) => r.key)), ...byKey.keys()]) {
      if (!this.keyOrder.has(key)) this.keyOrder.set(key, this.keyOrder.size);
    }
    const rows = [];
    for (const [key, points] of byKey) {
      const after = this.lastPoint.get(key) ?? -Infinity;
      const info = unitOf.get(key) || {};
      points.sort((a, b) => a[0] - b[0]);
      for (const [t, value] of points) {
        if (t <= after) continue;
        rows.push({ t, key, label: info.unit ? `${key} (${info.unit})` : key, value: typeof value === 'number' ? value : typeof value === 'boolean' ? Number(value) : null,
          unit: info.unit, meaning: info.meaning, group: info.group, order: this.keyOrder.get(key) });
      }
      if (points.length) this.lastPoint.set(key, Math.max(after, points[points.length - 1][0]));
    }
    if (rows.length) ds.append(rows);
    for (const [key, info] of unitOf) this.keys.set(key, info);
    this.valuesOk = true;
    this.feed.dispatchEvent(new CustomEvent('values', { detail: { path: this.path, watch: this.watch, streams: v.streams || [] } }));
    return rows.length;
  }
}
