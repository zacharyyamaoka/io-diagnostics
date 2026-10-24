// The Activity log's logic, free of the DOM (docs/activity-log-v1.md): the typed search, the filter pipeline, the volume
// strip's bins, the scope rule and the terminal-output line assembler. activity/log.js draws it; tests/test_activity_view.js
// runs it in node and in the real page.
//
// The typed search is Android Logcat's and macOS Console's (research: reports/media/activity-log-research-2026-10-23.html):
//   level:warn   that severity and above        code:Fault   a field contains the text (case-insensitive)
//   -code:Plugged   NOT      "two words"   quoted        /re/   a regular expression        bare words   all must match
// The same key is ORed, different keys are ANDed. The severity FLOOR is the toolbar's and search cannot lower it
// (Foxglove's rule): `level:debug` under a Warn+ floor still shows warn and above.
'use strict';
(function (root) {
  const RANK = { debug: 0, info: 1, warn: 2, error: 3, fatal: 4 };
  const FLOORS = ['debug', 'info', 'warn', 'error'];
  const sevRank = (severity) => (RANK[severity] === undefined ? 1 : RANK[severity]);
  const whenOf = (row) => (row.last_stamp !== null && row.last_stamp !== undefined ? row.last_stamp : row.stamp) || 0;

  // ------------------------------------------------------------------------------------------------ typed search ----
  const KEYS = {
    code: (row) => [row.code],
    entity: (row) => [row.name, row.path],
    path: (row) => [row.path],
    source: (row) => [row.source],
    type: (row) => [row.type, row.kind],
    kind: (row) => [row.kind],
    port: (row) => [row.port],
    hw: (row) => [row.hardware_id],
    hardware: (row) => [row.hardware_id],
    message: (row) => [row.message],
    msg: (row) => [row.message],
    run: (row) => [(row.args || {}).run_id],
    channel: (row) => [row.channel],
    fault: (row) => [(row.args || {}).fault, ...(row.badges || []).map((badge) => badge.fault)],
    id: (row) => [row.id],
  };
  const BARE = (row) => [row.message, row.code, row.name, row.path, row.source, row.kind];

  function tokens(text) {
    const out = [];
    const source = String(text || '');
    let i = 0;
    while (i < source.length) {
      while (i < source.length && /\s/.test(source[i])) i += 1;
      if (i >= source.length) break;
      let word = '';
      let quoted = false;
      while (i < source.length && (quoted || !/\s/.test(source[i]))) {
        if (source[i] === '"') quoted = !quoted;
        else word += source[i];
        i += 1;
      }
      if (word) out.push(word);
    }
    return out;
  }

  function matcher(value) {
    const re = value.match(/^\/(.+)\/(i?)$/);
    if (re) {
      try {
        const rx = new RegExp(re[1], 'i');
        return (text) => rx.test(String(text ?? ''));
      } catch (_) { return () => false; }
    }
    const needle = value.toLowerCase();
    return (text) => String(text ?? '').toLowerCase().includes(needle);
  }

  // text -> {clauses: [{neg, key, value, test(row)}], empty}
  function compile(text) {
    const clauses = [];
    let index = 0;
    for (let word of tokens(text)) {
      let neg = false;
      if (word.length > 1 && word[0] === '-') { neg = true; word = word.slice(1); }
      const colon = word.indexOf(':');
      const key = colon > 0 ? word.slice(0, colon).toLowerCase() : '';
      const value = colon > 0 ? word.slice(colon + 1) : word;
      if (key === 'level' && value) {
        const floor = value.replace(/\+$/, '').toLowerCase();
        if (floor in RANK) clauses.push({ neg, key: 'level', value, test: (row) => sevRank(row.severity) >= RANK[floor] });
        else clauses.push({ neg, key: 'level', value, test: () => false });
        continue;
      }
      if (key && KEYS[key] && value) {
        const test = matcher(value);
        clauses.push({ neg, key, value, test: (row) => KEYS[key](row).some(test) });
        continue;
      }
      const test = matcher(word);
      clauses.push({ neg, key: `text#${index}`, value: word, test: (row) => BARE(row).some(test) });
      index += 1;
    }
    return { clauses, empty: clauses.length === 0, text: String(text || '') };
  }

  // same key ORed, different keys ANDed, every negation must not match
  function matches(query, row) {
    if (!query || query.empty) return true;
    const groups = new Map();
    for (const clause of query.clauses) {
      if (clause.neg) { if (clause.test(row)) return false; continue; }
      if (!groups.has(clause.key)) groups.set(clause.key, false);
      if (clause.test(row)) groups.set(clause.key, true);
    }
    for (const ok of groups.values()) if (!ok) return false;
    return true;
  }

  // ------------------------------------------------------------------------------------------------ the pipeline ----
  const PRESETS = {
    all: () => true,
    faults: (row) => row.type === 'fault' || (row.badges || []).length > 0 || /^Fault/.test(row.code || ''),
    runs: (row) => row.type === 'run',
  };

  // rows (newest first) + the filter state -> what is shown, why the rest is not, which shown rows are highlighted.
  // state: {floor, preset, search (text), exclude [text], highlight [text], onlyHighlighted, range [t0, t1] | null}
  function applyFilters(rows, state) {
    const floor = RANK[state.floor] === undefined ? 1 : RANK[state.floor];
    const preset = PRESETS[state.preset] || PRESETS.all;
    const search = compile(state.search);
    const exclude = (state.exclude || []).map(compile);
    const highlight = (state.highlight || []).map(compile).filter((query) => !query.empty);
    const hidden = { floor: 0, preset: 0, range: 0, search: 0, exclude: 0, highlighted: 0 };
    const shown = [];
    const lit = new Set();
    for (const row of rows) {
      if (sevRank(row.severity) < floor) { hidden.floor += 1; continue; }
      if (!preset(row)) { hidden.preset += 1; continue; }
      if (state.range && (whenOf(row) < state.range[0] || whenOf(row) > state.range[1])) { hidden.range += 1; continue; }
      if (!matches(search, row)) { hidden.search += 1; continue; }
      if (exclude.some((query) => !query.empty && matches(query, row))) { hidden.exclude += 1; continue; }
      const isLit = highlight.some((query) => matches(query, row));
      if (state.onlyHighlighted && highlight.length && !isLit) { hidden.highlighted += 1; continue; }
      if (isLit) lit.add(row.id);
      shown.push(row);
    }
    return { shown, hidden, lit, hiddenTotal: Object.values(hidden).reduce((sum, n) => sum + n, 0) };
  }

  // ------------------------------------------------------------------------------------------- the volume strip ----
  // Rows -> `count` equal bins over [t0, t1] with a count per severity class: error (error + fatal), warn, quiet (info + debug).
  function bins(rows, t0, t1, count) {
    const span = Math.max(t1 - t0, 1e-6);
    const out = Array.from({ length: count }, (_, i) => ({ from: t0 + (span * i) / count, to: t0 + (span * (i + 1)) / count, error: 0, warn: 0, quiet: 0 }));
    for (const row of rows) {
      const at = whenOf(row);
      if (at < t0 || at > t1) continue;
      const bin = out[Math.min(count - 1, Math.floor(((at - t0) / span) * count))];
      const rank = sevRank(row.severity);
      bin[rank >= 3 ? 'error' : rank === 2 ? 'warn' : 'quiet'] += Math.max(row.count || 1, 1); // a row that stands for ×5 is five events
    }
    return out;
  }

  // ------------------------------------------------------------------------------------------------- the scope ----
  const below = (path, ancestor) => path === ancestor || path.startsWith(`${ancestor}/`);
  // The rule the host applies (logview.in_scope), for rows already loaded: the entity and everything below it (plus the
  // rows naming its hardware), optionally every ancestor up to the host.
  function inScope(row, scope) {
    if (!scope || !scope.path) return true;
    if (scope.hardware_id && row.hardware_id === scope.hardware_id) return true;
    if (scope.mode === 'self') return row.path === scope.path;
    if (below(row.path || '', scope.path)) return true;
    return scope.mode === 'host' && Boolean(row.path) && below(scope.path, row.path);
  }

  // ------------------------------------------------------------------------------- terminal output to lines ----
  // Bytes of a run's terminal (UTF-8, ANSI, \r progress redraws) -> the lines a log shows: escape sequences dropped, a
  // lone \r starts the line over (a progress bar is one line, not two hundred), \n ends it. The unfinished line is last.
  function createLineAssembler(maxLines = 2000) {
    const decoder = typeof TextDecoder === 'function' ? new TextDecoder('utf-8') : null;
    const done = [];
    let current = '';
    let mode = 'text'; // text | esc | csi | osc | oscEsc
    let cr = false;
    let version = 0;
    function char(c) {
      if (mode === 'esc') { mode = c === '[' ? 'csi' : c === ']' ? 'osc' : 'text'; return; }
      if (mode === 'csi') { if (c >= '@' && c <= '~') mode = 'text'; return; }
      if (mode === 'osc') { if (c === '\x07') mode = 'text'; else if (c === '\x1b') mode = 'oscEsc'; return; }
      if (mode === 'oscEsc') { mode = 'text'; return; }
      if (cr) {
        cr = false;
        if (c === '\n') { done.push(current); current = ''; return; }
        current = '';
      }
      if (c === '\x1b') mode = 'esc';
      else if (c === '\r') cr = true;
      else if (c === '\n') { done.push(current); current = ''; }
      else if (c === '\b') current = current.slice(0, -1);
      else if (c === '\t') current += '    ';
      else if (c >= ' ' && c !== '\x7f') current += c;
    }
    return {
      push(bytes) {
        const text = typeof bytes === 'string' ? bytes : decoder ? decoder.decode(bytes, { stream: true }) : String.fromCharCode(...bytes);
        for (const c of text) char(c);
        if (done.length > maxLines) done.splice(0, done.length - maxLines);
        version += 1;
        return version;
      },
      lines() { return current ? [...done, current] : [...done]; },
      get version() { return version; },
    };
  }

  const api = { RANK, FLOORS, sevRank, whenOf, tokens, compile, matches, PRESETS, applyFilters, bins, inScope, createLineAssembler };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.IOActivityCore = api;
}(typeof window !== 'undefined' ? window : globalThis));
