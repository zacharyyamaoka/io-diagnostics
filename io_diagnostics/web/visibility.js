// Visibility: which rows the tree shows (Zach, 2026-10-24, after his Claude transcript app's "Show 308 more" and
// "Hide 5 hidden sessions" rows and its eye per row).
//
//   window.IOVisibility = { arrange(rows, {revealed}), idOf(row), isHidden(row), mode, choices, set(id, choice),
//                           toggleMode(), toggleReveal(key), subscribe(fn), load(), mount() }
//
// The rules (all of them Zach's words):
//   - Hidden BY DEFAULT: an OS device no driver plugin claims (keyboard, mouse, webcam, WiFi, Ethernet): "If no driver
//     or something has been registered then likely it's not even needed". The API says so: node.claimed === false.
//     If he wants one he brings it back here; "if you actually wanted to track it you should create a driver/diagnostics
//     status thing".
//   - Each group that has hidden rows ends in one quiet row, "Show N hidden"; open, it reads "Hide N hidden".
//   - The control lives in the bottom status bar ("Visibility"): a mode in which every row shows an eye to hide or
//     unhide it. "Not something I imagine doing that often", so it is not on every row at rest.
//   - A hidden row NEVER hides a fault. Its problems still count in the header and on its parent (the API's counts and
//     hints are untouched: this only filters what the tree draws), and a hidden row that reports a WARN or ERROR of its
//     own shows itself again until it is acknowledged (hide it once more while it shows: choice "ack"). The
//     acknowledgement ends when the problem does, so the next one shows the row again.
//   - Persistence: the choices are kept with the app's settings (POST /api/v1/settings {visibility: {id: choice}}), so
//     both channels and the tray agree; localStorage is the fallback when the host cannot take them.
//
// Loads in the page (window.IOVisibility) and in Node (module.exports) so tests/test_view_core.js can run arrange().
'use strict';
(function (root) {
  const STORE_KEY = 'io-diagnostics.visibility';
  const CHOICES = ['hide', 'show', 'ack'];
  const state = { mode: false, choices: {}, revealed: new Set(), listeners: new Set(), loaded: false, pending: {}, timer: null, source: 'memory' };

  // The id a choice is kept under: the hardware id (a replug to another port keeps it), else the entity path.
  const idOf = (row) => String(row.hardware_id || row.entity || row.key || '');
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };

  // WHY warn too (Zach, 2026-10-24: "a hidden device's own WARN/ERROR still surfaces"): a device with a driver has a status;
  // one that reports a problem of its own is not "not needed". A problem only BELOW it (an error under a hidden group) also
  // shows it, so the cause is never out of sight.
  const ownError = (row) => {
    const status = row.status || {};
    return status.own === 'error' || status.own === 'warn' || (status.hint && status.hint.level === 'error')
      || (status.part && (status.part.level === 'error' || status.part.level === 'warn'));
  };
  const defaultHidden = (row) => row.claimed === false && Boolean(row.entity);

  // {hidden, surfaced}: hidden by the rules; surfaced = it would be hidden but is in error, so it shows.
  function isHidden(row, choices = state.choices) {
    const choice = choices[idOf(row)];
    const wanted = choice === 'hide' || choice === 'ack' || (choice !== 'show' && defaultHidden(row));
    if (!wanted) return { hidden: false, surfaced: false };
    if (choice !== 'ack' && ownError(row)) return { hidden: false, surfaced: true };
    return { hidden: true, surfaced: false };
  }

  const worstOf = (rows) => {
    let worst = null;
    for (const row of rows) {
      const status = row.status || {};
      for (const level of [status.own, status.part && status.part.level, status.hint && status.hint.level]) {
        if (level && level !== 'stale' && level in RANK && RANK[level] > 0 && (!worst || RANK[level] > RANK[worst])) worst = level;
      }
    }
    return worst;
  }

  // The rows to draw: hidden ones leave their list and a "Show N hidden" row ends it; revealed, they stay (dimmed) above
  // a "Hide N hidden" row. `querying` shows everything (a search must find a hidden row). Pure: the input is not changed.
  function arrange(rows, { revealed = state.revealed, choices = state.choices, querying = false, parentKey = '' } = {}) {
    const out = [];
    const hidden = [];
    for (const row of rows) {
      const verdict = querying ? { hidden: false, surfaced: false } : isHidden(row, choices);
      const children = row.children && row.children.length ? arrange(row.children, { revealed, choices, querying, parentKey: row.key }) : row.children;
      const copy = children === row.children ? { ...row } : { ...row, children };
      if (verdict.surfaced) copy.surfaced = true;
      (verdict.hidden ? hidden : out).push(copy);
    }
    if (!hidden.length) return out;
    const open = revealed.has(parentKey);
    const first = rows[0] || {};
    const bundle = {
      key: `${parentKey || 'root'}#hidden`, entity: null, bundle: true, kind: 'hidden-bundle', icon: 'group', label: open ? `Hide ${hidden.length} hidden` : `Show ${hidden.length} hidden`,
      name: '', bundleOf: parentKey, depth: first.depth, parent: first.parent === undefined ? null : first.parent, children: [], parts: [], attrs: [], columns: {}, hiddenCount: hidden.length,
      worst: worstOf(hidden), open, status: { own: null, hint: null, part: null, note: '' }, defaultOpen: false, connected: true, idText: '', title: '',
    };
    return open ? [...out, ...hidden.map((row) => ({ ...row, dimmed: true })), bundle] : [...out, bundle];
  }

  // ------------------------------------------------------------------------------------------- state ----
  const notify = () => { for (const listener of [...state.listeners]) { try { listener(); } catch (error) { console.error('visibility listener:', error); } } };

  function store() {
    try { root.localStorage.setItem(STORE_KEY, JSON.stringify(state.choices)); } catch (error) { /* private window: fine */ }
  }

  function flush() {
    state.timer = null;
    const changes = state.pending;
    state.pending = {};
    if (!Object.keys(changes).length || typeof root.fetch !== 'function') return;
    root.fetch('/api/v1/settings', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' },
      body: JSON.stringify({ visibility: changes }) })
      .then((response) => { state.source = response.ok ? 'settings' : 'browser'; })
      .catch(() => { state.source = 'browser'; });
  }

  function set(id, choice) {
    if (!id) return;
    if (choice && !CHOICES.includes(choice)) return;
    if (choice) state.choices[id] = choice; else delete state.choices[id];
    state.pending[id] = choice || null;
    store();
    if (state.timer === null && typeof root.setTimeout === 'function') state.timer = root.setTimeout(flush, 250);
    notify();
  }

  // What the eye does on a row: bring a hidden row back, or hide a shown one (an error that surfaced it is acknowledged).
  function toggleRow(row) {
    const verdict = isHidden(row);
    const id = idOf(row);
    if (verdict.hidden) set(id, 'show');
    else if (verdict.surfaced) set(id, 'ack');
    else set(id, 'hide');
  }

  // An acknowledgement ends when the error does, so the next error shows the row again.
  function expireAcks(rows) {
    (function walk(list) {
      for (const row of list) {
        if (state.choices[idOf(row)] === 'ack' && !ownError(row)) set(idOf(row), 'hide');
        walk(row.children || []);
      }
    })(rows);
  }

  function toggleMode(on) {
    state.mode = on === undefined ? !state.mode : Boolean(on);
    if (root.document) {
      root.document.documentElement.classList.toggle('io-vis-mode', state.mode);
      const button = root.document.getElementById('vis-toggle');
      if (button) button.setAttribute('aria-pressed', String(state.mode));
    }
    notify();
  }

  function toggleReveal(key) {
    if (state.revealed.has(key)) state.revealed.delete(key); else state.revealed.add(key);
    notify();
  }

  function load() {
    try {
      const saved = JSON.parse(root.localStorage.getItem(STORE_KEY) || '{}');
      for (const [id, choice] of Object.entries(saved)) if (CHOICES.includes(choice)) state.choices[id] = choice;
    } catch (error) { /* nothing stored */ }
    if (typeof root.fetch !== 'function') return Promise.resolve();
    return root.fetch('/api/v1/settings', { signal: AbortSignal.timeout(8000) }).then((response) => response.json()).then((settings) => {
      if (settings && settings.visibility && typeof settings.visibility === 'object') {
        // the host's choices win; what only the browser has is offered to the host once
        const fromHost = settings.visibility;
        const onlyHere = Object.entries(state.choices).filter(([id, choice]) => fromHost[id] !== choice);
        state.choices = { ...fromHost };
        if (Object.keys(fromHost).length === 0 && onlyHere.length) for (const [id, choice] of onlyHere) { state.choices[id] = choice; state.pending[id] = choice; }
        state.source = 'settings';
        store();
        if (Object.keys(state.pending).length && state.timer === null) state.timer = root.setTimeout(flush, 250);
        notify();
      }
      state.loaded = true;
    }).catch(() => { state.loaded = true; });
  }

  // The "Visibility" entry in the bottom status bar: a toggle, quiet, not on every row at rest.
  function mount() {
    const doc = root.document;
    if (!doc || doc.getElementById('vis-toggle')) return;
    const bar = doc.querySelector('.statusbar');
    if (!bar) return;
    const button = doc.createElement('button');
    button.type = 'button';
    button.id = 'vis-toggle';
    button.className = 'sb-btn';
    button.setAttribute('aria-pressed', String(state.mode));
    button.title = 'Visibility: show an eye on every row to hide or unhide it. Devices no driver claims are hidden by default.';
    button.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8Z"/><circle cx="8" cy="8" r="2"/></svg><span>Visibility</span>';
    button.addEventListener('click', () => toggleMode());
    const anchor = doc.getElementById('runs-status') || doc.getElementById('controls');
    if (anchor) bar.insertBefore(button, anchor); else bar.appendChild(button);
  }

  const IOVisibility = {
    CHOICES, arrange, idOf, isHidden, defaultHidden, worstOf, set, toggleRow, toggleMode, toggleReveal, expireAcks, load, mount,
    subscribe(listener) { state.listeners.add(listener); return () => state.listeners.delete(listener); },
    get mode() { return state.mode; },
    get choices() { return state.choices; },
    get revealed() { return state.revealed; },
    get source() { return state.source; },
    reset() { state.choices = {}; state.revealed = new Set(); state.pending = {}; state.mode = false; },
  };
  root.IOVisibility = IOVisibility;
  if (typeof module !== 'undefined' && module.exports) module.exports = IOVisibility;
  if (root.document) {
    const start = () => { mount(); load(); };
    if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', start); else start();
  }
})(typeof window !== 'undefined' ? window : globalThis);
