// The Activity log view (docs/activity-log-v1.md): window.IOActivityLog. One calm table of what happened, newest first,
// modelled on rqt_console (columns, exclude / highlight lists) with a Logcat-style typed search, a severity floor the
// search cannot lower, a live tail that pauses when you look away, and a volume strip by severity over time.
//
// What it reads: GET /api/v1/activity?fold=1 (the host's folded rows: a fault on its cause, a run as one row, repeats
// counted; docs/api-v1.md) and, while the page is open, GET /api/v1/events?fold=1 as the signal that something changed.
// Two channels: Activity (coded events) and Raw (uncoded lines: an entity's kernel lines, and the output of its runs).
// Raw is off, except in an entity's own log.
//
// WHY an instance you can mount anywhere: the Activity view is one (IOActivityLog.main), the entity page's own log is
// another (IOActivityLog.create({scope, compact})), and runs/r4.js draws a run's output under its row by attaching a
// provider (IOActivityLog.attachRuns). Every boundary drags, remembers its size (double-click resets, arrows move it).
'use strict';
(function () {
  const C = window.IOActivityCore;
  if (!C) { console.error('activity/core.js did not load'); return; }
  const esc = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const PREF = 'io-diagnostics.activity.';
  const read = (key, fallback) => { try { const v = localStorage.getItem(PREF + key); return v === null ? fallback : JSON.parse(v); } catch (_) { return fallback; } };
  const write = (key, value) => { try { localStorage.setItem(PREF + key, JSON.stringify(value)); } catch (_) { /* private mode: the choice lasts this page only */ } };

  const LIMIT = 2000;        // rows asked of the host
  const SHOW_STEP = 300;     // rows drawn at once; the footer says how many more
  const RAW_LIMIT = 1000;
  const STRIP_BINS = 72;
  const KNOWN_MAX = 6000;    // rows remembered so a paused list can keep one the host has since folded away
  const BREADCRUMB_S = 30;   // a fault's detail shows what its entity logged this long before
  const PAUSE_PX = 8;        // scrolled this far from the top = looking away
  const COLUMNS = [['n', 'No', 46], ['t', 'Time', 72], ['sev', 'Severity', 84], ['ent', 'Entity', 150], ['code', 'Code', 170], ['msg', 'Message', 0], ['src', 'Source', 150]];
  const RESIZABLE = ['ent', 'code', 'src'];
  const FLOOR_LABEL = { debug: 'Debug+', info: 'Info+', warn: 'Warn+', error: 'Error+' };
  const SCOPE_MODES = [['children', '+ children'], ['self', 'this only'], ['host', '+ path to host']];
  const shape = (level) => (level === 'warn' || level === 'error' || level === 'fatal'
    ? `<svg class="shape lv-${level === 'fatal' ? 'error' : level}" role="img" aria-label="${level}"><use href="#s-${level === 'fatal' ? 'error' : level}"/></svg>` : '');
  const lastSegment = (path) => String(path || '').split('/').pop() || 'host';
  const shortSource = (source) => String(source || '').replace(/\s*[(·].*$/, '').replace(/^io_facts\./, '').replace(/^io_diagnostics\./, '');

  const registry = { instances: new Set(), runs: null, fallback: null, main: null, visible: false };
  // The output of runs under their rows: the provider a run view attached (runs/r4.js), else one on the runs runtime (runs/core.js)
  // that every page has, so an entity's own log shows its runs' output whichever run view is chosen.
  const effective = () => registry.runs || (window.IORuns ? (registry.fallback = registry.fallback || runsProvider(window.IORuns)) : null);

  // ============================================================================================ run output ====
  // Bytes of a run's terminal -> the lines under its row (activity/core.js createLineAssembler), one stream per run being shown.
  // `api`: {stream(id, onData, onEnd) -> stop(), get(id) -> run | null, elapsed(run), sendInput(id, text), interrupt(id)} (IORuns has them).
  function runsProvider(api) {
    const keepFinished = 8; // finished runs whose lines stay in memory (an expanded row asks for them again)
    const runs = new Map(); // id -> {assembler, stop, refs, ended}
    let timer = null;
    const changed = () => {
      if (timer) return;
      timer = setTimeout(() => { timer = null; window.IOActivityLog.outputChanged(); }, 120);
    };
    return {
      lines(id) { const entry = runs.get(id); return entry ? entry.assembler.lines() : []; },
      state(id) {
        const run = api.get(id);
        return run ? { active: Boolean(run.active), interactive: Boolean(run.interactive), elapsed: api.elapsed(run), state: run.state } : null;
      },
      watch(id) {
        let entry = runs.get(id);
        if (!entry) {
          entry = { assembler: C.createLineAssembler(), refs: 0, ended: false, stop: null };
          runs.set(id, entry);
          entry.stop = api.stream(id, (bytes) => { entry.assembler.push(bytes); changed(); }, () => { entry.ended = true; changed(); });
          const ended = [...runs.entries()].filter(([, other]) => other.ended && other.refs === 0);
          for (const [key, old] of ended.slice(0, Math.max(0, ended.length - keepFinished))) { old.stop(); runs.delete(key); }
        }
        entry.refs += 1;
      },
      unwatch(id) { const entry = runs.get(id); if (entry) entry.refs = Math.max(0, entry.refs - 1); },
      send: (id, text) => api.sendInput(id, text),
      stop: (id) => api.interrupt(id),
      dispose() { clearTimeout(timer); for (const entry of runs.values()) entry.stop(); runs.clear(); },
    };
  }

  // ================================================================================================ the instance ====
  function create(opts = {}) {
    // a compact log (inside an entity page) keeps nothing in the browser: its filters are the page's, not the person's
    const saved = (name, fallback) => (opts.compact ? fallback : read(name, fallback));
    const s = {
      floor: saved('floor', opts.floor || 'info'), preset: 'all', search: '', exclude: saved('exclude', []), highlight: saved('highlight', []),
      onlyHighlighted: false, raw: opts.raw === undefined ? null : opts.raw, scope: opts.scope || null, range: null,
      expanded: null, limit: SHOW_STEP, paused: false, frozen: null, newCount: 0,
      rows: [], rawRows: [], clock: null, log: null, error: '', loaded: false, optsOpen: false,
      cols: saved('cols', {}), detailW: saved('detail-w', 380), stripH: saved('strip-h', 44),
    };
    const known = new Map();      // id -> the last row object seen (a paused list keeps rows that the host folded away)
    let el = null;
    let timer = null;
    let source = null;
    let again = false;
    let shownIds = [];
    let lastHtml = { list: '', detail: '', strip: '', chips: '' };
    let watching = new Set();
    const inst = { opts, s };

    // ------------------------------------------------------------------------------------------------ data ----
    const rawOn = () => (s.raw === null ? Boolean(s.scope) : s.raw);
    async function getJson(url) {
      const response = await fetch(url, { cache: 'no-store' });
      const data = await response.json();
      if (data.error) throw new Error(data.error);
      return data;
    }
    const scopeQuery = (query) => { if (s.scope && s.scope.path) { query.set('path', s.scope.path); query.set('scope', s.scope.mode); } return query; };
    let inflight = null;
    function load() {
      if (inflight) { again = true; return inflight; }
      inflight = (async () => {
        try {
          const activity = await getJson(`/api/v1/activity?${scopeQuery(new URLSearchParams({ fold: '1', limit: String(LIMIT), severity: 'debug' }))}`);
          const raw = rawOn() ? await getJson(`/api/v1/activity?${scopeQuery(new URLSearchParams({ fold: '1', channel: 'raw', limit: String(RAW_LIMIT), severity: 'debug' }))}`) : null;
          s.rows = activity.events;
          s.rawRows = raw ? raw.events : [];
          s.clock = activity.clock;
          s.log = activity.log;
          s.error = '';
          s.loaded = true;
          for (const row of [...s.rows, ...s.rawRows]) known.set(row.id, row);
          // a long-open page must not keep every row it ever saw: the oldest go first (a Map keeps insertion order)
          for (const id of [...known.keys()].slice(0, Math.max(0, known.size - KNOWN_MAX))) known.delete(id);
        } catch (error) {
          s.error = String(error.message || error);
        }
        draw();
      })().finally(() => {
        inflight = null;
        if (again) { again = false; load(); }
      });
      return inflight;
    }
    function connect() {
      disconnect();
      const query = scopeQuery(new URLSearchParams({ fold: '1', severity: 'debug' }));
      try {
        source = new EventSource(`/api/v1/events?${query}`);
        let pending = null;
        const soon = () => { if (!pending) pending = setTimeout(() => { pending = null; load(); }, 250); };
        // WHY only `activity`: a tick comes every refresh whether or not a row changed, and a load is the whole window
        source.addEventListener('activity', soon);
      } catch (_) { source = null; }
      // WHY also a poll: a row the host folded away sends no event, and a stream that fell over must not leave a stale log
      timer = setInterval(load, 8000);
    }
    function disconnect() {
      if (source) { source.close(); source = null; }
      clearInterval(timer);
      timer = null;
    }

    // ------------------------------------------------------------------------------------- what is on screen ----
    const filterState = () => ({ floor: s.floor, preset: s.preset, search: s.search, exclude: s.exclude, highlight: s.highlight, onlyHighlighted: s.onlyHighlighted, range: s.range });
    function allRows() {
      const rows = [...s.rows, ...(rawOn() ? s.rawRows : [])];
      rows.sort((a, b) => C.whenOf(b) - C.whenOf(a) || (b.seq || 0) - (a.seq || 0));
      return rows;
    }
    function compute() {
      const rows = allRows();
      const result = C.applyFilters(rows, filterState());
      let list = result.shown;
      if (s.paused && s.refreeze) { s.frozen = { order: list.map((row) => row.id), set: new Set(list.map((row) => row.id)) }; s.refreeze = false; }
      if (s.paused && s.frozen) {
        // the order is frozen, the rows are not: a row keeps updating in place and one a filter now hides is gone (a changed filter
        // re-freezes); a row the host has folded away since (absent from the load) stays as the viewer last saw it
        const fresh = list.filter((row) => !s.frozen.set.has(row.id));
        s.newCount = fresh.length;
        const byId = new Map(list.map((row) => [row.id, row]));
        const present = new Set(rows.map((row) => row.id));
        list = s.frozen.order.map((id) => byId.get(id) || (present.has(id) ? null : known.get(id))).filter(Boolean);
      } else s.newCount = 0;
      return { rows, result, list };
    }
    const refilter = () => { if (s.paused) s.refreeze = true; }; // a changed filter is a new question: the paused list is re-frozen on its answer
    function pause() {
      if (s.paused) return;
      s.frozen = { order: [...shownIds], set: new Set(shownIds) };
      s.paused = true;
    }
    function resume() {
      s.paused = false;
      s.frozen = null;
      s.newCount = 0;
      s.expanded = null;
      const list = el && el.querySelector('.al-list');
      if (list) list.scrollTop = 0;
    }

    // ------------------------------------------------------------------------------------------------- html ----
    function badgeHtml(badge) {
      const ended = badge.state === 'healed' ? ` · healed ${esc(badge.ended_time)}` : badge.state === 'cleared' ? ` · cleared ${esc(badge.ended_time)}` : '';
      return `<span class="al-badge" title="${esc(badge.message)}">→ ${esc(badge.label)}${ended}</span>`;
    }
    function runChip(row) {
      const a = row.args || {};
      // WHY only while it goes: an ended run's message already says how it ended and how long it took
      const going = a.state === 'running' ? '<span class="al-run" title="the run is going">running…</span> ' : '';
      return `${going}<button class="al-link" data-run-open="${esc(a.run_id)}" title="Show this run's output">Show output</button>`;
    }
    function messageHtml(row) {
      const x = row.count > 1 ? ` <span class="al-x" title="${row.count} identical events, first ${esc(row.time)}, last ${esc(row.last_time)}">×${row.count}</span>` : '';
      const badges = (row.badges || []).map(badgeHtml).join('');
      const run = row.type === 'run' && row.code === 'Run' ? ` ${runChip(row)}` : '';
      return `<span class="al-text">${esc(row.message)}</span>${x}${badges ? ` ${badges}` : ''}${run}`;
    }
    function rowHtml(row, index, lit) {
      const diagnostic = C.sevRank(row.severity) >= 2;
      const raw = row.channel === 'raw';
      const boot = row.code === 'Booted' || row.code === 'ShutDown';
      const classes = ['ev', 'al-row', diagnostic ? `sev-${row.severity === 'fatal' ? 'error' : row.severity}` : '', raw ? 'al-raw' : '', boot ? 'al-boot' : '',
        lit.has(row.id) ? 'al-hl' : '', s.expanded === row.id ? 'al-sel' : '', row.severity === 'debug' ? 'al-debug' : ''].filter(Boolean).join(' ');
      const when = row.last_time || row.time;
      const title = `first ${row.iso || ''}${row.last_iso ? ` · last ${row.last_iso}` : ''}${row.received_iso ? ` · host saw it ${row.received_iso}` : ''}`;
      const name = row.name || lastSegment(row.path);
      const code = row.code ? `<span class="kind" title="${esc(row.kind)}">${esc(row.code)}</span>` : '<span class="muted al-none">—</span>';
      return `<div class="${classes}" role="row" data-al-row="${esc(row.id)}"${diagnostic ? ' data-diagnostic' : ''} aria-selected="${s.expanded === row.id}">`
        + `<span class="al-n">${index}</span><span class="t" title="${esc(title)}">${esc(when)}</span>`
        + `<span class="al-sev">${shape(row.severity)}<span class="al-sevw">${esc(row.severity)}</span></span>`
        + `<span class="al-ent"><button class="al-entbtn" data-al-scope="${esc(row.path)}" data-name="${esc(name)}" title="Show only ${esc(row.path)} and what is below it">${esc(name)}</button></span>`
        + `<span class="al-code">${code}</span><span class="al-msg">${messageHtml(row)}</span>`
        + `<span class="src" title="${esc(row.source)}">${esc(shortSource(row.source))}</span></div>`;
    }
    function outputHtml(row) {
      const runs = effective();
      const id = (row.args || {}).run_id;
      if (!runs || row.type !== 'run' || !id || !outputWanted(row)) return '';
      const info = runs.state(id) || {};
      const lines = runs.lines(id);
      const text = lines.slice(-400).map(esc).join('\n');
      const act = info.active && info.interactive;
      const moves = (row.args || {}).danger === 'moves';
      return `<div class="al-out" data-al-out="${esc(id)}"><div class="al-out-h"><span>Output · ${info.active ? 'running' : 'finished'}${info.elapsed ? ` ${esc(info.elapsed)}` : ''}</span>`
        + `${act ? `<button class="al-stop${moves ? ' moves' : ''}" data-al-stop="${esc(id)}">${moves ? 'Stop (motor goes limp)' : 'Stop'}</button>` : ''}</div>`
        + `<pre class="al-out-pre" tabindex="0">${text || '<span class="muted">waiting for output…</span>'}</pre>`
        + `${act ? `<form class="al-in" data-al-in="${esc(id)}"><input data-keep="in-${esc(id)}" autocomplete="off" spellcheck="false" placeholder="Type a reply, Enter sends it to the run" aria-label="Send input to the run"><button type="submit">Send</button></form>` : ''}</div>`;
    }
    function outputWanted(row) {
      if ((row.args || {}).state === 'running') return true; // a run going streams under its row wherever it is shown
      if (s.expanded === row.id) return true;
      return Boolean(s.scope) && rawOn() && inst.recentRuns && inst.recentRuns.has((row.args || {}).run_id);
    }
    function dayOf(row) { return String(row.last_iso || row.iso || '').slice(0, 10); }
    function listHtml(c) {
      const { list, result } = c;
      const slice = list.slice(0, s.limit);
      shownIds = list.map((row) => row.id);
      const total = c.rows.length;
      // the rows are numbered by their place in the loaded window, oldest = 1: a filter leaves gaps, the footer counts them
      const order = new Map(c.rows.slice().reverse().map((row, i) => [row.id, i + 1]));
      inst.recentRuns = new Set(slice.filter((row) => row.type === 'run').slice(0, 5).map((row) => (row.args || {}).run_id));
      let html = '';
      let day = '';
      for (const row of slice) {
        const rowDay = dayOf(row);
        if (rowDay !== day) { day = rowDay; html += `<div class="al-day" role="presentation">${esc(rowDay)}</div>`; }
        html += rowHtml(row, order.get(row.id) || '', result.lit) + outputHtml(row);
      }
      if (list.length > slice.length) html += `<button class="al-more" data-al-more>Show ${Math.min(SHOW_STEP, list.length - slice.length)} more (${list.length - slice.length} not drawn)</button>`;
      if (!list.length) html += emptyHtml(c, total);
      return html;
    }
    function emptyHtml(c, total) {
      if (!s.loaded) return '<div class="al-empty muted">Loading the log…</div>';
      if (s.error) return `<div class="al-empty err">${esc(s.error)}</div>`;
      if (!total) return '<div class="al-empty muted">Nothing has been logged yet.</div>';
      const h = c.result.hidden;
      const why = [];
      if (h.floor) why.push(`${h.floor} below the ${FLOOR_LABEL[s.floor]} floor`);
      if (h.search) why.push(`${h.search} not matching the search`);
      if (h.exclude) why.push(`${h.exclude} hidden by your filters`);
      if (h.preset) why.push(`${h.preset} outside this view`);
      if (h.range) why.push(`${h.range} outside the selected time`);
      return `<div class="al-empty muted">Nothing here. ${why.length ? `${esc(why.join(' · '))}.` : ''}</div>`;
    }
    function headerHtml() {
      // WHY not an .ev: `.act .ev` is the log's rows (the colour gates and the tests count them); the header is no event
      return `<div class="al-head" role="row">${COLUMNS.map(([id, label]) => `<span class="al-h al-h-${id}">${label}`
        + `${RESIZABLE.includes(id) ? `<i class="al-colsash" data-al-col="${id}" role="separator" aria-orientation="vertical" aria-label="Resize ${label}" tabindex="0" title="Drag to resize · double-click to reset"></i>` : ''}</span>`).join('')}</div>`;
    }

    function chipsHtml() {
      const out = [];
      if (s.scope && s.scope.path) {
        out.push(`<span class="al-chip al-scope"><span class="al-chip-k">Log of</span> <b title="${esc(s.scope.path)}">${esc(s.scope.name || lastSegment(s.scope.path))}</b>`
          + `<span class="seg small al-modes" role="radiogroup" aria-label="Scope">${SCOPE_MODES.map(([mode, label]) => `<button role="radio" aria-checked="${s.scope.mode === mode}" class="${s.scope.mode === mode ? 'on' : ''}" data-al-mode="${mode}">${label}</button>`).join('')}</span>`
          + `${opts.fixedScope ? '' : '<button class="al-x-btn" data-al-clear-scope title="Back to the whole computer" aria-label="Clear the scope">×</button>'}</span>`);
      }
      if (s.range) out.push(`<span class="al-chip"><span class="al-chip-k">Time</span> <b>${esc(fmtClock(s.range[0]))}–${esc(fmtClock(s.range[1]))}</b><button class="al-x-btn" data-al-clear-range aria-label="Clear the time range">×</button></span>`);
      s.exclude.forEach((text, i) => out.push(`<span class="al-chip"><span class="al-chip-k">Hide</span> <b>${esc(text)}</b><button class="al-x-btn" data-al-drop="exclude:${i}" aria-label="Stop hiding ${esc(text)}">×</button></span>`));
      s.highlight.forEach((text, i) => out.push(`<span class="al-chip al-hlchip"><span class="al-chip-k">Highlight</span> <b>${esc(text)}</b><button class="al-x-btn" data-al-drop="highlight:${i}" aria-label="Stop highlighting ${esc(text)}">×</button></span>`));
      return out.join('');
    }
    function fmtClock(seconds) {
      const offset = s.clock && s.clock.utc_offset_s !== undefined && s.clock.utc_offset_s !== null ? s.clock.utc_offset_s * 1000 : -new Date().getTimezoneOffset() * 60000;
      return new Date(seconds * 1000 + offset).toISOString().slice(11, 19);
    }

    // the volume strip: every row that passes scope, search and filters except the floor, the view and the time range
    function stripHtml(c) {
      const rows = C.applyFilters(c.rows, { ...filterState(), floor: 'debug', preset: 'all', range: null }).shown;
      if (!rows.length) return '<div class="al-strip-empty muted">no events yet</div>';
      let t0 = Infinity;
      let t1 = -Infinity;
      for (const row of rows) { t0 = Math.min(t0, C.whenOf(row)); t1 = Math.max(t1, C.whenOf(row)); }
      if (s.clock) t1 = Math.max(t1, s.clock.now);
      if (t1 - t0 < 60) t0 = t1 - 60;
      inst.strip = { t0, t1 };
      const bins = C.bins(rows, t0, t1, STRIP_BINS);
      const peak = Math.max(1, ...bins.map((bin) => bin.error + bin.warn + bin.quiet));
      const bars = bins.map((bin) => {
        const total = bin.error + bin.warn + bin.quiet;
        const height = total ? Math.max(8, Math.round((Math.log2(total + 1) / Math.log2(peak + 1)) * 100)) : 0;
        const part = (n, cls) => (n ? `<i class="${cls}" style="flex:${n}"${cls !== 'q' ? ' data-diagnostic' : ''}></i>` : '');
        return `<b class="al-b" style="height:${height}%" title="${esc(fmtClock(bin.from))} · ${bin.error} error · ${bin.warn} warn · ${bin.quiet} other">${part(bin.error, 'e')}${part(bin.warn, 'w')}${part(bin.quiet, 'q')}</b>`;
      }).join('');
      const sel = s.range ? `<span class="al-sel-range" style="left:${((s.range[0] - t0) / (t1 - t0)) * 100}%;width:${((s.range[1] - s.range[0]) / (t1 - t0)) * 100}%"></span>` : '';
      return `<div class="al-bars" role="img" aria-label="Events over time by severity">${bars}${sel}</div>`
        + `<div class="al-axis"><span>${esc(fmtClock(t0))}</span><span>${esc(fmtClock(t1))}</span></div>`;
    }

    function summaryText(c) {
      const now = s.clock ? s.clock.now : Date.now() / 1000;
      const recent = s.rows.filter((row) => C.whenOf(row) >= now - 3600 && row.channel !== 'raw');
      const errors = recent.filter((row) => C.sevRank(row.severity) >= 3).length;
      const warns = recent.filter((row) => C.sevRank(row.severity) === 2).length;
      if (!s.loaded) return 'loading…';
      if (errors || warns) return `${errors ? `${errors} error${errors === 1 ? '' : 's'}` : ''}${errors && warns ? ' · ' : ''}${warns ? `${warns} warning${warns === 1 ? '' : 's'}` : ''} in the last hour`;
      return 'nothing needs you in the last hour';
    }

    function detailHtml(row) {
      if (!row) return '';
      const a = row.args || {};
      const kv = (pairs) => `<div class="kv">${pairs.filter(Boolean).map(([k, v]) => `<div class="k">${esc(k)}</div><div class="v">${v}</div>`).join('')}</div>`;
      const mono = (text) => `<span class="mono">${esc(text)}</span>`;
      const when = [`first ${mono(row.iso || '—')}`, row.last_iso ? `last ${mono(row.last_iso)} · ×${row.count}` : '', row.received_iso ? `the host saw it ${mono(row.received_iso)}` : ''].filter(Boolean).join('<br>');
      const members = (row.members || []).length ? `<h4>Folded into this row</h4>${row.members.map((m) => `<div class="al-member"><span class="t">${esc(m.time)}</span> <span class="kind">${esc(m.code)}</span> ${esc(m.message)}</div>`).join('')}` : '';
      const faults = row.code === 'FoundAtStart' && Array.isArray(a.faults)
        ? `<h4>Faults found</h4>${a.faults.map((f) => `<div class="al-member"><button class="al-entbtn" data-al-scope="${esc(f.path)}" data-name="${esc(lastSegment(f.path))}">${esc(lastSegment(f.path))}</button> <span class="kind">${esc(f.fault)}</span> ${esc(f.message)}</div>`).join('')}` : '';
      const argRows = Object.entries(a).filter(([k]) => !['faults', 'merged'].includes(k)).map(([k, v]) => [k, mono(typeof v === 'string' ? v : JSON.stringify(v))]);
      const crumbs = breadcrumbs(row);
      const journal = journalCommand(row);
      const code = row.code || '';
      const actions = [
        `<button class="al-act" data-al-scope="${esc(row.path)}" data-name="${esc(row.name || lastSegment(row.path))}">Show only this entity</button>`,
        `<a class="link al-act" data-go="${esc(row.path)}">Open its page</a>`,
        code ? `<button class="al-act" data-al-hide="code:${esc(code)}">Hide ${esc(code)}</button><button class="al-act" data-al-light="code:${esc(code)}">Highlight ${esc(code)}</button>` : '',
        `<button class="al-act" data-al-copy>Copy as JSON</button>`,
      ].filter(Boolean).join('');
      return `<div class="al-d-head"><div class="al-d-title">${shape(row.severity)}<span>${esc(row.message)}</span></div>`
        + `<button class="al-x-btn" data-al-close aria-label="Close the detail" title="Close (Esc)">×</button></div>`
        + kv([['code', code ? `<span class="kind">${esc(code)}</span> <span class="muted">${esc(row.kind)}</span>` : '<span class="muted">a raw line: no code</span>'],
          ['severity', esc(row.severity)], ['entity', `${esc(row.name || '')} ${mono(row.path)}`], ['when', when], ['source', mono(row.source || '—')],
          row.hardware_id ? ['hardware', mono(row.hardware_id)] : null, row.port ? ['port', mono(row.port)] : null, row.seq !== null && row.seq !== undefined ? ['seq', mono(row.seq)] : null,
          ['id', mono(row.id)]])
        + `<div class="al-d-actions">${actions}</div>${members}${faults}`
        + (argRows.length ? `<h4>Details</h4>${kv(argRows)}` : '')
        + (crumbs ? `<h4>Just before (${BREADCRUMB_S} s)</h4>${crumbs}` : '')
        + (journal ? `<h4>In the system journal</h4><div class="al-cmd"><code>${esc(journal)}</code><button class="al-act" data-al-copy-text="${esc(journal)}">Copy</button></div>` : '');
    }
    function breadcrumbs(row) {
      if (!((row.badges || []).length || C.sevRank(row.severity) >= 3) && row.code !== 'FoundAtStart') return '';
      const at = C.whenOf(row);
      const before = allRows().filter((r) => r.id !== row.id && C.whenOf(r) < at && C.whenOf(r) >= at - BREADCRUMB_S && (r.path === row.path || r.path.startsWith(`${row.path}/`) || row.path.startsWith(`${r.path}/`)));
      return before.slice(0, 8).map((r) => `<div class="al-member"><span class="t">${esc(r.time)}</span> <span class="kind">${esc(r.code || 'raw')}</span> ${esc(r.message)}</div>`).join('');
    }
    function journalCommand(row) {
      const kernel = /journal|kernel_log|kernel/.test(String(row.source || '')) || row.channel === 'raw';
      if (!kernel || !row.iso) return '';
      const since = row.iso.slice(0, 19).replace('T', ' ');
      const port = row.port ? ` --grep 'usb ${row.port}[: ]'` : '';
      return `journalctl -k -o short-iso --since "${since}" -n 40${port}`;
    }

    // ----------------------------------------------------------------------------------------------- drawing ----
    function keepFocus(fn) {
      const active = document.activeElement;
      const keep = active && el && el.contains(active) && active.dataset ? active.dataset.keep : null;
      const start = keep ? active.selectionStart : 0;
      const value = keep ? active.value : '';
      const scrolls = new Map([...el.querySelectorAll('.al-out-pre')].map((pre) => [pre.closest('[data-al-out]').dataset.alOut, [pre.scrollTop, pre.scrollHeight - pre.scrollTop - pre.clientHeight < 6]]));
      fn();
      for (const pre of el.querySelectorAll('.al-out-pre')) {
        const saved = scrolls.get(pre.closest('[data-al-out]').dataset.alOut);
        pre.scrollTop = !saved || saved[1] ? pre.scrollHeight : saved[0]; // follows the end unless the viewer scrolled up
      }
      if (keep) {
        const again = el.querySelector(`[data-keep="${keep}"]`);
        if (again) { again.value = value; again.focus(); try { again.setSelectionRange(start, start); } catch (_) { /* not a text field */ } }
      }
    }
    function syncRunWatch() {
      const runs = effective();
      if (!runs) return;
      const want = new Set([...el.querySelectorAll('[data-al-out]')].map((node) => node.dataset.alOut));
      for (const id of want) if (!watching.has(id)) runs.watch(id);
      for (const id of watching) if (!want.has(id)) runs.unwatch(id);
      watching = want;
    }
    function draw() {
      if (!el || !registry.visible && !opts.compact) return;
      const c = compute();
      const list = el.querySelector('.al-list');
      const html = headerHtml() + listHtml(c);
      if (html !== lastHtml.list) {
        lastHtml.list = html;
        const top = list.scrollTop;
        keepFocus(() => { list.innerHTML = html; });
        list.scrollTop = top;
      }
      const chips = chipsHtml();
      if (chips !== lastHtml.chips) { lastHtml.chips = chips; el.querySelector('.al-chips').innerHTML = chips; el.querySelector('.al-chips').hidden = !chips; }
      const strip = opts.compact ? '' : stripHtml(c);
      if (strip !== lastHtml.strip && !opts.compact) { lastHtml.strip = strip; el.querySelector('.al-strip').innerHTML = strip; }
      const pane = el.querySelector('.al-detail'); // a compact log has none: its rows open in place
      const row = pane && s.expanded ? known.get(s.expanded) : null;
      const detail = detailHtml(row);
      if (pane && detail !== lastHtml.detail) { lastHtml.detail = detail; pane.innerHTML = detail; }
      el.classList.toggle('al-detail-open', Boolean(row));
      syncToolbar(c);
      syncRunWatch();
    }
    function syncToolbar(c) {
      const q = (selector) => el.querySelector(selector);
      q('.al-count').textContent = s.loaded ? `· ${c.rows.length} rows${s.log && s.log.matching > s.rows.length ? ` of ${s.log.matching}` : ''} · newest first · ${summaryText(c)}` : '· loading…';
      el.querySelectorAll('[data-al-floor]').forEach((b) => { const on = b.dataset.alFloor === s.floor; b.classList.toggle('on', on); b.setAttribute('aria-checked', String(on)); });
      el.querySelectorAll('[data-al-preset]').forEach((b) => { const on = b.dataset.alPreset === s.preset; b.classList.toggle('on', on); b.setAttribute('aria-checked', String(on)); });
      const raw = q('[data-al-raw]');
      raw.setAttribute('aria-pressed', String(rawOn()));
      raw.classList.toggle('on', rawOn());
      raw.title = s.raw === null ? (s.scope ? 'Raw lines are on in an entity\'s own log' : 'Raw lines (kernel lines, run output) are off outside an entity\'s own log') : 'Raw lines';
      const live = q('[data-al-live]');
      live.classList.toggle('paused', s.paused);
      live.textContent = s.paused ? (s.newCount ? `Paused · Resume (${s.newCount} new)` : 'Paused · Resume') : 'Live';
      live.setAttribute('aria-pressed', String(s.paused));
      const foot = q('.al-foot');
      const h = c.result.hidden;
      const hiddenTotal = c.result.hiddenTotal;
      foot.textContent = `${c.result.shown.length} shown`
        + (hiddenTotal ? ` · ${hiddenTotal} hidden (${[h.floor && `${h.floor} below ${FLOOR_LABEL[s.floor]}`, h.search && `${h.search} by search`, h.exclude && `${h.exclude} by Hide`, h.preset && `${h.preset} by view`, h.range && `${h.range} by time`, h.highlighted && `${h.highlighted} not highlighted`].filter(Boolean).join(', ')})` : '')
        + (s.search && /\blevel:/.test(s.search) && C.compile(s.search).clauses.some((cl) => cl.key === 'level' && C.RANK[cl.value.replace(/\+$/, '')] < C.RANK[s.floor]) ? ' · the search cannot go below the severity floor' : '')
        + (s.log && s.log.note ? ` · ${s.log.note}` : '');
      q('.al-optsbtn').setAttribute('aria-expanded', String(s.optsOpen));
      q('.al-opts').hidden = !s.optsOpen;
      q('[data-al-onlyhl]').checked = s.onlyHighlighted;
      el.style.setProperty('--al-strip-h', `${s.stripH}px`);
      el.style.setProperty('--al-detail-w', `${s.detailW}px`);
      for (const [id, , width] of COLUMNS) if (RESIZABLE.includes(id)) el.style.setProperty(`--al-w-${id}`, `${s.cols[id] || width}px`);
    }

    // ----------------------------------------------------------------------------------------------- mounting ----
    function skeleton() {
      return `<div class="act al${opts.compact ? ' al-compact' : ''}"><div class="al-bar">`
        + (opts.compact ? '<span class="act-count al-count"></span>' : `<h2>Activity <span class="act-count al-count"></span></h2>`)
        + (opts.compact ? '' : `<div class="seg small act-filter" role="radiogroup" aria-label="Show">${[['all', 'All'], ['faults', 'Faults'], ['runs', 'Runs']].map(([id, label]) => `<button data-al-preset="${id}" role="radio" aria-checked="false">${label}</button>`).join('')}</div>`)
        + `<div class="seg small al-floor" role="radiogroup" aria-label="Severity floor">${C.FLOORS.map((f) => `<button data-al-floor="${f}" role="radio" aria-checked="false" title="Show ${FLOOR_LABEL[f]}: ${f} and worse. The search cannot go below this.">${FLOOR_LABEL[f]}</button>`).join('')}</div>`
        + `<button class="al-toggle" data-al-raw aria-pressed="false">Raw</button>`
        + `<label class="search al-search"><svg class="ic"><use href="#i-search"/></svg><input data-keep="al-search" class="al-searchbox" autocomplete="off" spellcheck="false" aria-label="Search the log"`
        + ` placeholder="Search · level:warn  code:Fault  -code:Plugged  entity:CAN" value="${esc(s.search)}"></label>`
        + `<button class="al-toggle al-optsbtn" data-al-opts aria-expanded="false" aria-haspopup="dialog"${opts.compact ? ' hidden' : ''}>Filters</button>`
        + `<button class="al-toggle al-live" data-al-live aria-pressed="false">Live</button>`
        + `<div class="al-opts" role="dialog" aria-label="Hide and highlight" hidden>`
        + `<label><span>Hide rows matching</span><input data-keep="al-hide" data-al-add="exclude" placeholder="code:ProbeBlocked  — press Enter" autocomplete="off" spellcheck="false"></label>`
        + `<label><span>Highlight rows matching</span><input data-keep="al-light" data-al-add="highlight" placeholder="entity:CAN 15  — press Enter" autocomplete="off" spellcheck="false"></label>`
        + `<label class="al-check"><input type="checkbox" data-al-onlyhl> Show highlighted only</label>`
        + `<div class="al-opts-foot"><button class="al-act" data-al-reset>Clear all filters</button><span class="muted">Same words as the search: field:text, -field:text, "quoted", /regex/.</span></div></div></div>`
        + `<div class="al-chips" hidden></div>`
        + (opts.compact ? '' : `<div class="al-strip" aria-label="Volume over time"></div><div class="al-hsash" role="separator" aria-orientation="horizontal" aria-label="Resize the volume strip" tabindex="0" title="Drag to resize · double-click to reset"></div>`)
        + `<div class="al-body"><div class="al-list" role="table" aria-label="Activity log" tabindex="0"></div>`
        + (opts.compact ? '' : `<div class="al-sash" role="separator" aria-orientation="vertical" aria-label="Resize the detail" tabindex="0" title="Drag to resize · double-click to reset"></div><aside class="al-detail" aria-label="Row detail"></aside>`)
        + `</div><div class="al-foot muted" role="status"></div></div>`;
    }
    inst.mount = function mount(target) {
      el = target;
      el.innerHTML = skeleton();
      el.hidden = false;
      wire();
      registry.instances.add(inst);
      if (opts.compact || registry.visible) start();
      draw();
      return inst;
    };
    function start() { if (!source) { load(); connect(); } }
    inst.start = start;
    inst.stop = () => { disconnect(); };
    inst.unmount = () => {
      disconnect();
      if (effective()) for (const id of watching) effective().unwatch(id);
      watching = new Set();
      registry.instances.delete(inst);
      if (el) el.innerHTML = '';
      el = null;
    };
    inst.redraw = () => { lastHtml.list = ''; draw(); };
    inst.resume = () => { resume(); lastHtml.list = ''; draw(); };
    inst.reload = () => load();
    inst.setScope = (scope) => {
      s.scope = scope && scope.path ? { path: scope.path, name: scope.name || lastSegment(scope.path), mode: scope.mode || 'children' } : null;
      s.raw = opts.raw === undefined ? null : opts.raw;
      s.rows = []; s.rawRows = []; s.expanded = null; s.paused = false; s.frozen = null; s.range = null;
      if (source) connect();
      load();
      draw();
    };
    // show one run: its entity's log, raw on, the run's row open (runs/r4.js `open`)
    inst.reveal = async (runId, run) => {
      const path = run && run.path;
      s.preset = 'all';
      s.floor = 'debug';
      s.search = '';
      s.range = null;
      if (path && !(s.scope && C.inScope({ path, hardware_id: '' }, s.scope))) inst.setScope({ path, name: (run.label || '').split(' · ')[0], mode: 'children' });
      s.expanded = `run:${runId}`;
      await load();
      pause(); // opening a row pauses the tail: the list is frozen as it is after this load
      lastHtml.list = '';
      draw();
      const row = el && el.querySelector(`[data-al-row="${CSS.escape(s.expanded)}"]`);
      if (row) row.scrollIntoView({ block: 'start' });
    };
    // a run's output moved: rewrite the open output blocks in place (the rest of the list, and a half-typed reply, stay)
    inst.patchOutput = () => {
      const runs = effective();
      if (!el || !runs) return;
      for (const block of el.querySelectorAll('[data-al-out]')) {
        const id = block.dataset.alOut;
        const pre = block.querySelector('.al-out-pre');
        const near = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 6;
        pre.textContent = runs.lines(id).slice(-400).join('\n') || 'waiting for output…';
        if (near) pre.scrollTop = pre.scrollHeight;
        const info = runs.state(id) || {};
        const head = block.querySelector('.al-out-h span');
        if (head) head.textContent = `Output · ${info.active ? 'running' : 'finished'}${info.elapsed ? ` ${info.elapsed}` : ''}`;
        if (block.querySelector('.al-in') && !(info.active && info.interactive)) { lastHtml.list = ''; draw(); return; } // the run ended: the reply box goes
      }
    };

    // -------------------------------------------------------------------------------------------------- events ----
    function wire() {
      const root = el;
      const list = root.querySelector('.al-list');
      root.addEventListener('click', (event) => {
        const t = event.target;
        const go = (selector) => t.closest(selector);
        let hit;
        if ((hit = go('[data-al-floor]'))) { s.floor = hit.dataset.alFloor; if (!opts.compact) write('floor', s.floor); refilter(); return draw(); }
        if ((hit = go('[data-al-preset]'))) { s.preset = hit.dataset.alPreset; refilter(); return draw(); }
        if (go('[data-al-raw]')) { s.raw = !rawOn(); lastHtml.list = ''; load(); return draw(); }
        if (go('[data-al-live]')) { if (s.paused) resume(); else pause(); lastHtml.list = ''; return draw(); }
        if (go('[data-al-opts]')) { s.optsOpen = !s.optsOpen; return draw(); }
        if (go('[data-al-reset]')) { s.search = ''; s.exclude = []; s.highlight = []; s.onlyHighlighted = false; s.range = null; s.preset = 'all'; refilter(); persistLists(); root.querySelector('.al-searchbox').value = ''; return draw(); }
        if ((hit = go('[data-al-drop]'))) { const [kind, i] = hit.dataset.alDrop.split(':'); s[kind].splice(Number(i), 1); refilter(); persistLists(); return draw(); }
        if (go('[data-al-clear-range]')) { s.range = null; refilter(); return draw(); }
        if (go('[data-al-clear-scope]')) { inst.setScope(null); return undefined; }
        if ((hit = go('[data-al-mode]'))) { s.scope.mode = hit.dataset.alMode; inst.setScope({ ...s.scope }); return undefined; }
        if ((hit = go('[data-al-scope]'))) { inst.setScope({ path: hit.dataset.alScope, name: hit.dataset.name, mode: 'children' }); return undefined; }
        if ((hit = go('[data-al-hide]'))) { addTo('exclude', hit.dataset.alHide); return undefined; }
        if ((hit = go('[data-al-light]'))) { addTo('highlight', hit.dataset.alLight); return undefined; }
        if ((hit = go('[data-al-copy-text]'))) { copy(hit.dataset.alCopyText, hit); return undefined; }
        if (go('[data-al-copy]')) { copy(JSON.stringify(known.get(s.expanded) || {}, null, 2), go('[data-al-copy]')); return undefined; }
        if (go('[data-al-close]')) { s.expanded = null; return draw(); }
        if (go('[data-al-more]')) { s.limit += SHOW_STEP; lastHtml.list = ''; return draw(); }
        if ((hit = go('[data-al-stop]'))) { if (effective()) effective().stop(hit.dataset.alStop); return undefined; }
        if (go('[data-run-open], [data-go], .al-in, .al-out-pre, .al-opts, .al-chips, .al-detail a')) return undefined; // the page's own handlers
        if ((hit = go('[data-al-row]'))) {
          const id = hit.dataset.alRow;
          s.expanded = s.expanded === id ? null : id;
          if (s.expanded) { if (!s.paused) pause(); } else if (list.scrollTop <= PAUSE_PX) resume();
          lastHtml.list = '';
          return draw();
        }
        return undefined;
      });
      root.addEventListener('input', (event) => {
        if (event.target.classList.contains('al-searchbox')) { s.search = event.target.value; refilter(); draw(); }
        if (event.target.matches('[data-al-onlyhl]')) { s.onlyHighlighted = event.target.checked; refilter(); draw(); }
      });
      root.addEventListener('change', (event) => { if (event.target.matches('[data-al-onlyhl]')) { s.onlyHighlighted = event.target.checked; refilter(); draw(); } });
      root.addEventListener('keydown', (event) => {
        const t = event.target;
        if (t.matches('[data-al-add]') && event.key === 'Enter') { if (t.value.trim()) addTo(t.dataset.alAdd, t.value.trim()); t.value = ''; event.preventDefault(); return; }
        if (t.matches('.al-in input')) return; // typing to a run: nothing else may take the key
        if (event.key === 'Escape') {
          if (s.optsOpen) { s.optsOpen = false; draw(); event.stopPropagation(); return; }
          if (s.expanded) { s.expanded = null; if (list.scrollTop <= PAUSE_PX) resume(); lastHtml.list = ''; draw(); event.stopPropagation(); }
          return;
        }
        if (/input|textarea|select/i.test(t.tagName)) return;
        if (event.key === '/') { root.querySelector('.al-searchbox').focus(); event.preventDefault(); return; }
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
          const ids = shownIds;
          const at = ids.indexOf(s.expanded);
          const next = ids[Math.max(0, Math.min(ids.length - 1, at + (event.key === 'ArrowDown' ? 1 : -1)))];
          if (next) { s.expanded = next; pause(); lastHtml.list = ''; draw(); const row = list.querySelector(`[data-al-row="${CSS.escape(next)}"]`); if (row) row.scrollIntoView({ block: 'nearest' }); }
          event.preventDefault();
        }
      });
      root.addEventListener('submit', (event) => {
        const form = event.target.closest('[data-al-in]');
        if (!form) return;
        event.preventDefault();
        const input = form.querySelector('input');
        if (effective() && input.value !== '') { effective().send(form.dataset.alIn, `${input.value}\n`); input.value = ''; }
      });
      // looking away pauses the tail; coming back to the top resumes it (unless a row is open)
      list.addEventListener('scroll', () => {
        if (list.scrollTop > PAUSE_PX && !s.paused) { pause(); draw(); } else if (list.scrollTop <= PAUSE_PX && s.paused && !s.expanded) { resume(); draw(); }
      }, { passive: true });
      wireSashes(root);
      wireStrip(root);
    }
    function persistLists() { if (!opts.compact) { write('exclude', s.exclude); write('highlight', s.highlight); } }
    function addTo(kind, text) { if (!s[kind].includes(text)) s[kind].push(text); refilter(); persistLists(); draw(); }
    async function copy(text, button) {
      try { await navigator.clipboard.writeText(text); const old = button.textContent; button.textContent = 'Copied'; setTimeout(() => { button.textContent = old; }, 1200); } catch (_) { /* clipboard blocked */ }
    }

    // every boundary drags, remembers its size, resets on double-click and moves with the arrow keys
    function dragger(handle, { axis, get, set, min, max, reset, save }) {
      const clamp = (value) => Math.max(min(), Math.min(max(), value));
      handle.addEventListener('pointerdown', (event) => {
        handle.setPointerCapture(event.pointerId);
        const start = axis === 'x' ? event.clientX : event.clientY;
        const base = get();
        document.body.classList.add('resizing');
        const move = (e) => { set(clamp(base + (axis === 'x' ? start - e.clientX : e.clientY - start))); draw(); };
        const up = () => { document.body.classList.remove('resizing'); handle.removeEventListener('pointermove', move); handle.removeEventListener('pointerup', up); save(); };
        handle.addEventListener('pointermove', move);
        handle.addEventListener('pointerup', up);
      });
      handle.addEventListener('dblclick', () => { set(reset); save(); draw(); });
      handle.addEventListener('keydown', (event) => {
        const step = event.shiftKey ? 40 : 12;
        const sign = axis === 'x' ? (event.key === 'ArrowLeft' ? 1 : event.key === 'ArrowRight' ? -1 : 0) : (event.key === 'ArrowDown' ? 1 : event.key === 'ArrowUp' ? -1 : 0);
        if (!sign) return;
        set(clamp(get() + sign * step));
        save();
        draw();
        event.preventDefault();
      });
    }
    function wireSashes(root) {
      const detail = root.querySelector('.al-sash');
      if (detail) {
        dragger(detail, { axis: 'x', get: () => s.detailW, set: (v) => { s.detailW = v; }, min: () => 260, max: () => Math.max(300, root.clientWidth - 420), reset: 380, save: () => { if (!opts.compact) write('detail-w', s.detailW); } });
      }
      const strip = root.querySelector('.al-hsash');
      if (strip) dragger(strip, { axis: 'y', get: () => s.stripH, set: (v) => { s.stripH = v; }, min: () => 28, max: () => 200, reset: 44, save: () => write('strip-h', s.stripH) });
      // WHY delegated: the column headers are part of the list's HTML, which is redrawn (a new row, a filter); a handle bound
      // to one of them would be gone, and a drag in progress follows the pointer on the window, not on an element that may be replaced
      const base = (id) => COLUMNS.find((column) => column[0] === id)[2];
      const setCol = (id, width) => { s.cols[id] = Math.max(70, Math.min(480, width)); syncCols(); };
      const saveCols = () => { if (!opts.compact) write('cols', s.cols); };
      root.addEventListener('pointerdown', (event) => {
        const handle = event.target.closest('[data-al-col]');
        if (!handle || event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const id = handle.dataset.alCol;
        const x0 = event.clientX;
        const w0 = s.cols[id] || base(id);
        document.body.classList.add('resizing'); // a column grows to the right of its left edge: dragging right widens it
        const move = (e) => setCol(id, w0 + e.clientX - x0);
        const up = () => { document.body.classList.remove('resizing'); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); saveCols(); };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
      });
      root.addEventListener('dblclick', (event) => {
        const handle = event.target.closest('[data-al-col]');
        if (handle) { delete s.cols[handle.dataset.alCol]; saveCols(); syncCols(); }
      });
      root.addEventListener('keydown', (event) => {
        const handle = event.target.closest && event.target.closest('[data-al-col]');
        const sign = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
        if (!handle || !sign) return;
        const id = handle.dataset.alCol;
        setCol(id, (s.cols[id] || base(id)) + sign * (event.shiftKey ? 40 : 12));
        saveCols();
        event.preventDefault();
      });
    }
    function syncCols() { for (const [id, , width] of COLUMNS) if (RESIZABLE.includes(id)) el.style.setProperty(`--al-w-${id}`, `${s.cols[id] || width}px`); }
    // drag across the strip = a time range; a click on it clears the range
    function wireStrip(root) {
      const strip = root.querySelector('.al-strip');
      if (!strip) return;
      strip.addEventListener('pointerdown', (event) => {
        const bars = strip.querySelector('.al-bars');
        if (!bars || !inst.strip) return;
        const box = bars.getBoundingClientRect();
        const at = (x) => inst.strip.t0 + Math.max(0, Math.min(1, (x - box.left) / box.width)) * (inst.strip.t1 - inst.strip.t0);
        const from = at(event.clientX);
        const x0 = event.clientX;
        strip.setPointerCapture(event.pointerId);
        const move = (e) => { if (Math.abs(e.clientX - x0) > 3) { s.range = [Math.min(from, at(e.clientX)), Math.max(from, at(e.clientX))]; refilter(); draw(); } };
        const up = (e) => {
          strip.removeEventListener('pointermove', move);
          strip.removeEventListener('pointerup', up);
          if (Math.abs(e.clientX - x0) <= 3) { s.range = null; refilter(); draw(); }
        };
        strip.addEventListener('pointermove', move);
        strip.addEventListener('pointerup', up);
      });
    }

    return inst;
  }

  // ============================================================================================= the registry ====
  window.IOActivityLog = {
    create,
    // the Activity view: one instance, drawn into `el`, streaming only while the view is visible
    main(el) {
      if (!registry.main) registry.main = create({}).mount(el);
      return registry.main;
    },
    setVisible(visible) {
      if (registry.visible === visible) return; // the page calls this on every render (once a second): only a change does anything
      registry.visible = visible;
      if (!registry.main) return;
      if (visible) { registry.main.start(); registry.main.redraw(); } else registry.main.stop();
    },
    // a run view (runs/r4.js) hands the log the live output of runs: {lines(id), state(id), watch(id), unwatch(id), send(id, text), stop(id)}
    runsProvider,
    attachRuns(provider) { registry.runs = provider; for (const inst of registry.instances) inst.redraw(); },
    detachRuns(provider) { if (registry.runs === provider) { registry.runs = null; for (const inst of registry.instances) inst.redraw(); } },
    redraw() { for (const inst of registry.instances) inst.redraw(); },
    outputChanged() { for (const inst of registry.instances) inst.patchOutput(); },
    get runs() { return registry.runs; },
    get effectiveRuns() { return effective(); },
  };
}());
