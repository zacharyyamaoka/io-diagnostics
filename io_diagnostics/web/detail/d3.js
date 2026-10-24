// Detail layout d3 · Readable summary (round 3 proposal lane d3; contract: docs/round2-seams.md §6).
// A Notion-like page that reads as sentences first (precedents: Linear's issue page, Apple's Battery-health line):
// one plain sentence of what is wrong and what to do, then the diagnostics status table (each row a toggle block that
// opens in place: values, sparklines, what to check), then "What changed" over the app-wide history window as dated
// sentences, then the entity's children. A diagnostic's title opens its own page (breadcrumb Computer › entity ›
// diagnostic). The Computer leads with its busiest resource. The utilities are the page's, in the entity header's
// Utilities ▾ (round 3.5); the lead sentence's "Update firmware" opens that menu.
// WHY sentences before the table: Zach, 2026-10-23: "What are the most important things to first show as soon as you
// click it? … it tells me specifically the diagnostics message", and "quiet by default and only call out the things
// with errors": every OK row is muted text, colour only on true diagnostic results ([data-diagnostic]).
// The page owns the data, the selection, the drill (ctx.diagnostic: back / forward work) and the utility flows; the
// history and health bar come from its shared cache (ctx.history, ctx.bar), the window from ctx.settings; this file
// reads only a folded part's /api/v1/node itself (cached, refreshed every few seconds; the page redraws every second).
'use strict';
(function (root) {
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const rank = (level) => (level in RANK ? RANK[level] : -1);
  const BINS = 96;               // the bars are 3 px wide, 2 px apart (health.js): 96 of them fill the 480 px bar; a child's mini bar re-bins
  const HIDDEN_VALUES = new Set(['what_to_check', 'kernel_evidence']);

  // This layout's own view state (the page's state S is not ours to write).
  const D3 = {
    open: new Set(),      // diagnostic blocks opened in place
    shut: new Set(),      // blocks the viewer closed (the first problem's block opens by itself)
    folds: new Set(['inside']), // toggle blocks below the table that are open (Inside open, the rest folded)
    allChanges: false,
    cache: new Map(),     // url → {data, error, at, loading}
    last: null,           // the last render call, for an immediate redraw after one of our own clicks
  };

  // ------------------------------------------------------------------------------------------ data ----
  function want(url, maxAgeMs) {
    const now = Date.now();
    let entry = D3.cache.get(url);
    if (!entry || (!entry.loading && now - entry.at > maxAgeMs)) {
      entry = { data: entry ? entry.data : undefined, error: null, at: now, loading: true };
      D3.cache.set(url, entry);
      const done = entry;
      fetch(url).then((response) => response.json().then((body) => {
        if (!response.ok || body.error) throw new Error(body.error || `HTTP ${response.status}`);
        return body;
      })).then((body) => { done.data = body; done.error = null; })
        .catch((error) => { done.error = String(error.message || error); })
        .finally(() => { done.loading = false; done.at = Date.now(); });
      if (D3.cache.size > 80) {
        for (const [key, old] of D3.cache) if (!old.loading && now - old.at > 60000) D3.cache.delete(key);
      }
    }
    return entry;
  }
  const q = encodeURIComponent;
  // the history and the health bar come from the page's shared cache (round 3.5); a folded part's node stays ours
  const history = (path) => D3.last.ctx.history(path, { everyMs: 4000 });
  const bar = (path) => D3.last.ctx.bar(path, BINS, { everyMs: 4000 });
  const nodeOf = (path) => want(`/api/v1/node?path=${q(path)}`, 3000);

  // ---------------------------------------------------------------------------------------- words ----
  const clock = (t) => {
    const d = new Date(t * 1000);
    return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':');
  };
  const windowWords = (seconds) => (seconds >= 3600 ? `${+(seconds / 3600).toFixed(1)} h` : seconds >= 60 ? `${Math.round(seconds / 60)} min` : `${Math.round(seconds)} s`);
  const shortName = (name, prefix) => (prefix && String(name).startsWith(`${prefix}: `) ? String(name).slice(prefix.length + 2) : String(name));
  const plainTitle = (title) => String(title || '').replace(/…$/, '');
  const valuesOf = (check) => Object.fromEntries((check.values || []).map((value) => [value.key, value.value]));
  const stepsOf = (check) => String(valuesOf(check).what_to_check || '').split('\n').filter((line) => line.trim());
  const listWords = (items) => (items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`);
  const verb = { error: 'is in error', warn: 'warns', stale: 'has gone quiet', unknown: "can't tell" };
  const keyOf = (entity, name) => `${entity}␟${name}`;

  // The diagnostics this row stands for: its own checks, then the checks of the parts folded into it (a board's
  // driver and motor, an adapter's CAN bus), worst first. A part's checks come from its own /api/v1/node.
  function diagnostics(ctx) {
    const detail = ctx.detail;
    const label = ctx.row ? ctx.row.label : detail.node.name;
    const list = detail.checks.map((check) => ({ check, entity: detail.node.path, owner: detail.node.name, part: null, label: shortName(check.name, detail.node.name) }));
    const parts = ((ctx.row && ctx.row.parts) || []).filter((part) => part.node && part.node.checks > 0);
    let loading = false;
    for (const part of parts) {
      const entry = nodeOf(part.entity);
      if (!entry.data) { loading = loading || !entry.error; continue; }
      for (const check of entry.data.checks) {
        list.push({ check, entity: part.entity, owner: part.name, part, label: `${part.label} · ${shortName(check.name, part.name)}` });
      }
    }
    list.forEach((item, at) => { item.at = at; item.key = keyOf(item.entity, item.check.name); item.knock = Boolean((ctx.byPath.get(item.entity) || {}).knock_on_of); });
    list.sort((a, b) => (rank(b.check.effective_level) - rank(a.check.effective_level)) || a.at - b.at);
    return { list, loading, label, parts };
  }

  // ------------------------------------------------------------------------------- small drawings ----
  // A measurement's trend over the window: a neutral line; null (unreadable) breaks it; its limits as faint rules.
  function spark(series, from, to, { w = 120, h = 18, big = false, fill = false } = {}) {
    const points = ((series && series.points) || []).map(([t, v]) => [t, typeof v === 'boolean' ? Number(v) : v]);
    const numbers = points.filter(([, v]) => typeof v === 'number' && Number.isFinite(v)).map(([, v]) => v);
    if (!numbers.length) return `<span class="d3-nodata">no data in the window</span>`;
    const percent = series.unit === '%';
    let lo = percent ? 0 : Math.min(...numbers);
    let hi = percent ? Math.max(100, ...numbers) : Math.max(...numbers);
    if (hi - lo < 1e-9) { lo -= 1; hi += 1; }
    const pad = big ? 4 : 2;
    const x = (t) => ((t - from) / Math.max(1e-9, to - from)) * w;
    const y = (v) => h - pad - ((v - lo) / (hi - lo)) * (h - 2 * pad);
    const runs = [];
    let run = [];
    for (const [t, v] of points) {
      if (typeof v !== 'number' || !Number.isFinite(v)) { if (run.length) runs.push(run); run = []; continue; }
      run.push(`${x(t).toFixed(1)},${y(v).toFixed(1)}`);
    }
    if (run.length) runs.push(run);
    const lines = runs.map((r) => (r.length === 1
      ? `<circle cx="${r[0].split(',')[0]}" cy="${r[0].split(',')[1]}" r="${big ? 2.2 : 1.6}" class="d3-dot"/>`
      : `<polyline points="${r.join(' ')}" class="d3-line"/>`)).join('');
    const limits = ['warn_low', 'warn_high', 'error_low', 'error_high'].map((k) => series[k]).filter((v) => typeof v === 'number' && v > lo && v < hi)
      .map((v) => `<line x1="0" x2="${w}" y1="${y(v).toFixed(1)}" y2="${y(v).toFixed(1)}" class="d3-limit"/>`).join('');
    const size = big || fill ? `width="100%" height="${h}" preserveAspectRatio="none"` : `width="${w}" height="${h}"`;
    return `<svg class="d3-spark${big ? ' big' : ''}${fill ? ' fill' : ''}" viewBox="0 0 ${w} ${h}" ${size} aria-hidden="true">${limits}${lines}</svg>`;
  }

  // One status's level over the window as the shared discrete state timeline (detail/health.js; Zach, 2026-10-24: separate
  // tall rectangles with a gap, never a line): its spans binned, the worst span's message in each cell's hover.
  function strip(levels, from, to) {
    return IOHealth.html(IOHealth.fromLevels(levels, from, to, IOHealth.count(to - from, 480, 'timeline')), { kind: 'timeline', label: 'level over the window' });
  }
  const escText = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  // The health bar (rqt_robot_monitor): the worst level any status held in each bin; null = no data yet. Drawn as the shared
  // discrete state timeline, re-binned to the count its width holds (a child's mini bar is narrow: few cells).
  function healthBar(cells, title, mini) {
    if (!cells || !cells.some(Boolean)) return '<span class="d3-nodata">no data in the window</span>';
    const kind = mini ? 'spark' : 'timeline';
    return IOHealth.html(IOHealth.rebin(IOHealth.fromBins(cells), IOHealth.count(900, mini ? 90 : 480, kind)), { kind, label: title || 'health over the window' });
  }

  // Measurements of one status: series that differ only by an index (CPU 0 Load … CPU 23 Load, usb1 … usb8 claimed %)
  // read as one row of bars, the rest one row each with its sparkline.
  function measurementRows(check, status, from, to, ctx, big) {
    const h = ctx.h;
    const series = new Map(((status && status.measurements) || []).map((m) => [m.key, m]));
    const groups = new Map();
    for (const m of check.measurements) {
      const pattern = m.key.replace(/\d+/, '#');
      if (pattern === m.key) continue;
      if (!groups.has(pattern)) groups.set(pattern, []);
      groups.get(pattern).push(m);
    }
    const grouped = new Set();
    let html = '';
    for (const [pattern, members] of groups) {
      if (members.length < 4 || new Set(members.map((m) => m.unit)).size > 1) continue;
      members.forEach((m) => grouped.add(m.key));
      const numbers = members.map((m) => (typeof m.value === 'number' ? m.value : 0));
      const top = members[0].unit === '%' ? Math.max(100, ...numbers) : Math.max(1e-9, ...numbers);
      const problem = members.some((m) => PROBLEM.has(m.level));
      const bars = members.map((m) => `<i style="height:${Math.max(4, Math.round(((typeof m.value === 'number' ? m.value : 0) / top) * 100))}%" class="${PROBLEM.has(m.level) ? `d3-lv-${m.level}` : ''}" title="${h.esc(`${m.key}: ${m.text}`)}"></i>`).join('');
      const peak = members.reduce((a, b) => ((typeof b.value === 'number' ? b.value : -Infinity) > (typeof a.value === 'number' ? a.value : -Infinity) ? b : a));
      html += `<div class="d3-m"${problem ? ' data-diagnostic' : ''}><span class="d3-mk">${h.esc(pattern.replace('#', '0…' + (members.length - 1)))}</span>`
        + `<span class="d3-cores" aria-label="${h.esc(pattern)}">${bars}</span><span class="d3-mv">max ${h.esc(peak.text)}</span></div>`;
    }
    for (const m of check.measurements) {
      if (grouped.has(m.key)) continue;
      const problem = PROBLEM.has(m.level);
      const trendSeries = series.get(m.key) ? { ...series.get(m.key), unit: m.unit } : null;
      const line = spark(trendSeries, from, to, big ? { w: 600, h: 44, big: true } : { w: 240, h: 18, fill: true });
      const seen = ((trendSeries && trendSeries.points) || []).map(([, v]) => v).filter((v) => typeof v === 'number' && Number.isFinite(v));
      const range = big && seen.length > 1 ? `<span class="d3-range">min ${h.esc(+Math.min(...seen).toPrecision(4))} · max ${h.esc(+Math.max(...seen).toPrecision(4))} · ${seen.length} samples</span>` : '';
      html += `<div class="d3-m${big ? ' big' : ''}"${problem ? ' data-diagnostic' : ''}><span class="d3-mk">${h.esc(m.key)}</span>`
        + `<span class="d3-ms">${line}</span><span class="d3-mv">${range}${h.shape(m.level)}${h.esc(m.text)}</span></div>`;
    }
    return html;
  }

  const statusOf = (entry, name) => (entry && entry.data ? entry.data.statuses.find((status) => status.name === name) : null);

  // ------------------------------------------------------------------------------- the lead sentence ----
  function actionHint(item, actions) {
    const ready = (name) => actions.find((action) => action.name === name && action.state !== 'nothing_to_do' && action.state !== 'blocked');
    const check = item.check;
    if (check.code === 'UpdateAvailable' || /firmware$/.test(check.name)) {
      const update = ready('update_firmware');
      if (update) return { action: update };
    }
    const steps = stepsOf(check);
    if (steps.length) return { steps };
    if (/fault/i.test(check.code || '') || /fault/.test(check.name)) {
      const clear = ready('clear_faults');
      if (clear) return { action: clear };
    }
    return null;
  }

  function leadHtml(ctx, info) {
    const h = ctx.h;
    const node = ctx.node;
    const actions = (ctx.detail && ctx.detail.actions) || [];
    const out = [];
    if (node.connected === false) out.push('Not connected.');
    if (node.knock_on_of) {
      const cause = ctx.byPath.get(node.knock_on_of);
      out.push(`Explained by <a class="link" data-go="${h.esc(node.knock_on_of)}">${h.esc(cause ? cause.name : node.knock_on_of)}</a> above${cause && cause.message ? `: ${h.esc(cause.message)}` : ''}. Fix that first; this row counts once, as that cause.`);
    }
    const problems = info.list.filter((item) => PROBLEM.has(item.check.effective_level));
    if (problems.length) {
      const first = problems[0];
      const level = first.check.effective_level;
      const subject = first.part ? `Its ${h.esc(first.part.label)}` : h.esc(info.list.filter((item) => !item.part).length > 1 ? `${info.label} · ${first.label}` : info.label);
      let sentence = `${h.shape(level, level)}<b>${subject}</b> ${verb[level]}: ${h.esc(first.check.message || first.check.code || '—')}`;
      const hint = actionHint(first, actions);
      if (hint && hint.action) sentence += `; <button class="d3-plain d3-act" data-util-menu>${h.esc(plainTitle(hint.action.title))}</button> is under Utilities.`;
      else if (hint && hint.steps) {
        sentence += `. Check first: ${h.esc(hint.steps[0].replace(/^\s*\d+[.)]\s*/, ''))}`;
        sentence += hint.steps.length > 1 ? ` <button class="d3-plain d3-act" data-d3-toggle="${h.esc(first.key)}" data-d3-force="1">${hint.steps.length - 1} more step${hint.steps.length > 2 ? 's' : ''}</button>.` : '.';
      } else sentence += '.';
      out.push(sentence);
      const rest = problems.length - 1;
      if (rest) out.push(`${rest} more diagnostic${rest > 1 ? 's need' : ' needs'} a look below.`);
    } else if (info.list.length) {
      const heard = node.age_s === null || node.age_s === undefined ? '' : `, last heard ${h.ago(node.age_s)} ago`;
      out.push(`All ${info.list.length} diagnostic${info.list.length > 1 ? 's' : ''} report OK${heard}.`);
    } else if (!info.loading) {
      out.push(node.own && node.own.level === null ? 'Nothing here reports a diagnostic of its own; it shows the worst below it.' : 'No diagnostics reported.');
    }
    // the Computer: its busiest resource right after its own verdict, before anything below it
    if (ctx.isComputer && ctx.detail) {
      const busy = busiest(ctx.detail.checks);
      if (busy) out.push(busy);
    }
    if (!problems.length && node.hint) {
      const origin = node.hint.origin;
      out.push(`Below it, <a class="link" data-go="${h.esc(origin)}">${h.esc(node.rollup.origin_name || origin)}</a> ${verb[node.hint.level] || `is ${h.esc(node.hint.level)}`}: ${h.esc(shortName(node.hint.message, node.rollup.origin_name))}.`);
    }
    return `<p class="d3-lead">${out.join(' ')}</p>`;
  }

  // The Computer's busiest resource, from the computer plugin's measurements (ROS diagnostic_common_diagnostics names).
  function busiest(checks) {
    const found = [];
    const add = (what, pct, detail = '') => { if (Number.isFinite(pct)) found.push({ what, pct, detail }); };
    for (const check of checks) {
      const m = Object.fromEntries(check.measurements.map((item) => [item.key, item]));
      const num = (key) => (m[key] && typeof m[key].value === 'number' ? m[key].value : NaN);
      for (const item of check.measurements) {
        if (item.key === 'CPU Load Average') add('CPU', item.value, m['CPU max core load'] ? `worst core ${m['CPU max core load'].text}` : '');
        else if (item.key === 'RAM Load Average') add('RAM', item.value, m['RAM used'] && m['RAM total'] ? `${m['RAM used'].text} of ${m['RAM total'].text}` : '');
        else if (item.key === 'swap used %') add('swap', item.value, m['swap used'] && m['swap total'] ? `${m['swap used'].text} of ${m['swap total'].text}` : '');
        else if (/ available$/.test(item.key) && item.unit === '%') {
          const mount = item.key.replace(/ available$/, '');
          add(`disk ${mount}`, 100 - item.value, m[`${mount} free`] ? `${m[`${mount} free`].text} free` : '');
        } else if (item.key === 'utilization' && item.unit === '%') add('GPU', item.value, m['memory used %'] ? `memory ${m['memory used %'].text}` : '');
        else if (/ claimed %$/.test(item.key)) add(`USB bus ${item.key.replace(/ claimed %$/, '').replace(/^usb/, '')}`, item.value);
        else if (/ link speed$/.test(item.key)) {
          const nic = item.key.replace(/ link speed$/, '');
          const speed = num(item.key);
          if (speed > 0) add(`network ${nic}`, (Math.max(num(`${nic} rx`) || 0, num(`${nic} tx`) || 0) / speed) * 100);
        }
      }
    }
    if (!found.length) return '';
    found.sort((a, b) => b.pct - a.pct);
    const words = (item) => `${item.what} at ${Math.round(item.pct)} %${item.detail ? ` (${item.detail})` : ''}`;
    const next = found.slice(1, 3).map((item) => `${item.what} ${Math.round(item.pct)} %`);
    return `Busiest right now: ${escText(words(found[0]))}${next.length ? `; then ${escText(listWords(next))}` : ''}.`;
  }

  // ------------------------------------------------------------------------------- the status table ----
  function blockHtml(item, ctx, hist) {
    const h = ctx.h;
    const check = item.check;
    const status = statusOf(hist, check.name);
    const from = hist && hist.data ? hist.data.from : 0;
    const to = hist && hist.data ? hist.data.to : 1;
    const steps = stepsOf(check);
    const values = check.values.filter((value) => !HIDDEN_VALUES.has(value.key)).map((value) => [value.key, h.esc(value.value)]);
    const caused = (check.caused_by || []).map((path) => `<a class="link" data-go="${h.esc(path)}">${h.esc((ctx.byPath.get(path) || {}).name || path)}</a>`);
    const timing = check.expected_period_s ? `every ${check.expected_period_s} s` : 'event-driven';
    const trend = status ? strip(status.levels, from, to) : '';
    return `<div class="d3-block">`
      + (caused.length ? `<div class="d3-p">Caused by ${listWords(caused)}.</div>` : '')
      + (steps.length ? `<div class="d3-steps">${steps.map((line) => `<div>${h.esc(line)}</div>`).join('')}</div>` : '')
      + (trend ? `<div class="d3-trend">${trend}</div>` : '')
      + (check.measurements.length ? `<div class="d3-ms-list">${measurementRows(check, status, from, to, ctx, false)}</div>` : '')
      + (values.length ? h.kv(values) : '')
      + `<div class="d3-meta">${check.code ? `code <span class="d3-code">${h.esc(check.code)}</span> · ` : ''}received ${h.esc((check.received_iso || '').slice(11, 19) || '—')} · ${h.ago(check.age_s)} ago · ${h.esc(timing)} · ${h.esc(check.source || '')}`
      + ` · <button class="d3-plain link" data-d3-open="${h.esc(item.key)}">open its page</button></div></div>`;
  }

  function tableHtml(ctx, info, hists) {
    const h = ctx.h;
    if (!info.list.length) return info.loading ? '<div class="d3-quiet">loading diagnostics…</div>' : '';
    const firstProblem = info.list.find((item) => PROBLEM.has(item.check.effective_level));
    return `<div class="d3-table" role="list">${info.list.map((item) => {
      const check = item.check;
      const level = check.effective_level;
      const problem = PROBLEM.has(level);
      const open = D3.open.has(item.key) || (firstProblem === item && !D3.shut.has(item.key));
      const word = problem ? `<span class="lvword lv-${h.esc(level)}${item.knock ? ' knock' : ''}">${h.esc(level)}</span>` : '';
      const row = `<div class="d3-row${problem ? ' prob' : ''}${item.knock ? ' knock' : ''}${open ? ' open' : ''}" role="listitem" data-d3-toggle="${h.esc(item.key)}"${problem ? ' data-diagnostic' : ''}>`
        + `<button class="d3-plain d3-chev" aria-expanded="${open}" title="${open ? 'Hide' : 'Show'} values" data-d3-toggle="${h.esc(item.key)}"><svg class="ic"><use href="#i-chev"/></svg></button>`
        + `<span class="d3-st${item.knock ? ' knock' : ''}">${h.shape(level, level)}</span>`
        + `<button class="d3-plain d3-name" data-d3-open="${h.esc(item.key)}" title="Open ${h.esc(check.name)}">${h.esc(item.label)}</button>`
        + `<span class="d3-msg" title="${h.esc(check.message || '')}">${word}${h.esc(check.message || check.code || '—')}</span>`
        + `<span class="d3-age" title="received ${h.esc(check.received_iso || '')}">${h.ago(check.age_s)}</span></div>`;
      return row + (open ? blockHtml(item, ctx, hists.get(item.entity)) : '');
    }).join('')}</div>`;
  }

  // What rolls up to this row's level, in one quiet line: its own checks, the parts folded into it, the hint, caused_by.
  function rollupLine(ctx, info) {
    const h = ctx.h;
    const node = ctx.node;
    const own = info.list.filter((item) => !item.part);
    const bits = [];
    if (node.own && node.own.level !== null) bits.push(`this row shows its own ${own.length} diagnostic${own.length === 1 ? '' : 's'}${node.level && node.level !== 'ok' ? ` (worst ${h.esc(node.level)})` : ''}`);
    else bits.push('this row reports nothing itself and shows the worst below');
    const folded = info.parts.filter((part) => part.node && part.node.checks > 0);
    if (folded.length) bits.push(`folded in: ${folded.map((part) => `${h.esc(part.label)}${part.problem ? ` (${h.esc(part.problem)})` : ''}`).join(', ')}`);
    const caused = info.list.filter((item) => (item.check.caused_by || []).length);
    for (const item of caused) bits.push(`${h.esc(item.label)} is caused by ${listWords(item.check.caused_by.map((path) => `<a class="link" data-go="${h.esc(path)}">${h.esc((ctx.byPath.get(path) || {}).name || path)}</a>`))}`);
    if (node.hint) bits.push(`below: <a class="link" data-go="${h.esc(node.hint.origin)}">${h.esc(node.rollup.origin_name || node.hint.origin)}</a> is ${h.esc(node.hint.level)}`);
    return `<div class="d3-rollup">Rolls up: ${bits.join(' · ')}.</div>`;
  }

  // ------------------------------------------------------------------------------- what changed ----
  function changes(hists, info, ctx) {
    const events = [];
    for (const [entity, entry] of hists) {
      if (!entry.data) continue;
      const owner = entity === ctx.detail.node.path ? ctx.detail.node.name : (info.parts.find((part) => part.entity === entity) || {}).name;
      const part = info.parts.find((p) => p.entity === entity);
      for (const status of entry.data.statuses) {
        const name = part ? `${part.label} · ${shortName(status.name, owner)}` : shortName(status.name, owner);
        status.levels.forEach((span, at) => {
          if (!at) return;
          const before = status.levels[at - 1].level;
          if (before === span.level) return;
          events.push({ t: span.from, name, from: before, to: span.level, message: span.message, code: span.code });
        });
      }
    }
    events.sort((a, b) => b.t - a.t);
    // first reports of OK at about the same time read as one sentence ("comms, firmware and config started, all OK")
    const merged = [];
    for (const event of events) {
      const prev = merged[merged.length - 1];
      if (event.from === null && event.to === 'ok' && prev && prev.start && Math.abs(prev.t - event.t) < 10) { prev.names.push(event.name); continue; }
      merged.push(event.from === null && event.to === 'ok' ? { ...event, start: true, names: [event.name] } : event);
    }
    return merged;
  }

  function changeSentence(event, ctx) {
    const h = ctx.h;
    const name = `<b>${h.esc(event.name)}</b>`;
    const msg = event.message ? `: ${h.esc(event.message)}` : '';
    const word = (level) => `<span class="lvword lv-${h.esc(level)}">${h.esc(level)}</span>`;
    if (event.start) {
      const names = event.names.map((n) => `<b>${h.esc(n)}</b>`);
      const said = names.length > 4 ? `${names.slice(0, 3).join(', ')} and ${names.length - 3} more` : listWords(names);
      return `${said} started reporting, ${names.length > 1 ? 'all ' : ''}OK.`;
    }
    if (event.to === null) return `${name} stopped reporting (its source no longer computes it).`;
    if (event.from === null) return `${name} first reported ${PROBLEM.has(event.to) ? word(event.to) : h.esc(event.to)}${msg}.`;
    if (event.to === 'stale') return `${name} went quiet${msg}.`;
    if (event.from === 'stale') return `${name} was heard again, ${PROBLEM.has(event.to) ? word(event.to) : 'OK'}${msg}.`;
    if (event.to === 'ok') return `${name} recovered to OK${msg}.`;
    if (event.from === 'ok') return `${name} turned ${word(event.to)}${msg}.`;
    return `${name} went from ${h.esc(event.from)} to ${word(event.to)}${msg}.`;
  }

  function changedHtml(ctx, info, hists, barEntry, windowS) {
    const h = ctx.h;
    const own = barEntry && barEntry.data ? barEntry.data.rows.find((row) => row.depth === 0) : null;
    const entityHist = hists.get(ctx.detail.node.path);
    const to = entityHist && entityHist.data ? entityHist.data.to : null;
    let html = '';
    if (own) {
      html += `<div class="d3-barline"><span class="d3-quiet">${h.esc(windowWords(windowS))} ago</span>${healthBar(own.bar, `${own.name}: the worst level held per ${barEntry.data.bin_s} s`)}<span class="d3-quiet">now</span></div>`;
    } else if (barEntry && barEntry.error) html += `<div class="d3-quiet">no health bar: ${h.esc(barEntry.error)}</div>`;
    if (!entityHist || (!entityHist.data && !entityHist.error)) return `${html}<div class="d3-quiet">loading history…</div>`;
    if (entityHist.error) return `${html}<div class="d3-quiet">no history for this row: ${h.esc(entityHist.error)}</div>`;
    const events = changes(hists, info, ctx);
    if (!events.length) return `${html}<div class="d3-quiet">Nothing changed in the last ${h.esc(windowWords(windowS))}.</div>`;
    const shown = D3.allChanges ? events : events.slice(0, 6);
    html += `<div class="d3-changes">${shown.map((event) => {
      const problem = PROBLEM.has(event.to);
      return `<div class="d3-ev"${problem ? ' data-diagnostic' : ''}><span class="d3-t">${clock(event.t)}</span><span class="d3-evs">${h.shape(event.to, event.to || '')}</span>`
        + `<span class="d3-evx">${changeSentence(event, ctx)}</span><span class="d3-age">${to ? `${h.ago(to - event.t)} ago` : ''}</span></div>`;
    }).join('')}</div>`;
    if (events.length > 6) html += `<button class="d3-plain d3-more" data-d3-allchanges>${D3.allChanges ? 'Show the latest 6' : `Show ${events.length - 6} earlier`}</button>`;
    return html;
  }

  // ------------------------------------------------------------------------------- folds below ----
  function fold(id, title, body, opts = {}) {
    if (!body) return '';
    const open = D3.folds.has(id) || Boolean(opts.force);
    return `<div class="d3-fold ${opts.cls || ''}" data-d3-foldbox="${id}"><button class="d3-plain d3-fold-t" data-d3-fold="${id}" aria-expanded="${open}">`
      + `<svg class="ic"><use href="#i-chev"/></svg><span>${title}</span>${opts.right ? `<span class="d3-quiet">${opts.right}</span>` : ''}</button>`
      + `${open ? `<div class="d3-fold-b">${body}</div>` : ''}</div>`;
  }

  function insideHtml(ctx, info, barEntry) {
    const h = ctx.h;
    const partPaths = new Set(info.parts.map((part) => part.entity));
    const children = (ctx.detail.node.children || []).filter((path) => !partPaths.has(path));
    if (!children.length) return { count: 0, html: '' };
    const bars = new Map(((barEntry && barEntry.data && barEntry.data.rows) || []).map((row) => [row.path, row]));
    const html = children.map((path) => {
      const child = ctx.byPath.get(path) || { name: path, icon: 'device', level: null, message: '' };
      const barRow = bars.get(path);
      const problem = PROBLEM.has(child.level) || (child.hint && PROBLEM.has(child.hint.level));
      return `<div class="d3-child" data-go="${h.esc(path)}" title="${h.esc(child.name)}">${h.icon(child.icon)}<span class="d3-cn">${h.esc(child.name)}</span>`
        + `<span class="d3-cm">${h.esc(child.message || '')}</span><span class="d3-cb">${barRow && barRow.bar.some(Boolean) ? healthBar(barRow.bar, '', true) : ''}</span>`
        + `<span class="d3-st${child.knock_on_of ? ' knock' : ''}">${problem ? h.shape(PROBLEM.has(child.level) ? child.level : child.hint.level, child.message) : ''}</span></div>`;
    }).join('');
    return { count: children.length, html };
  }

  // ------------------------------------------------------------------------------- the diagnostic page ----
  function diagnosticPage(ctx, info, item, hists) {
    const h = ctx.h;
    const check = item.check;
    const level = check.effective_level;
    const hist = hists.get(item.entity);
    const status = statusOf(hist, check.name);
    const from = hist && hist.data ? hist.data.from : 0;
    const to = hist && hist.data ? hist.data.to : 1;
    const top = ctx.state && ctx.state.tree ? ctx.state.tree.path : 'host';
    const entityLabel = info.label;
    const crumbs = `<nav class="d3-crumb"><a class="link" data-go="${h.esc(top)}">Computer</a><span>›</span>`
      + (ctx.node.path === top ? '' : `<button class="d3-plain link" data-d3-back>${h.esc(entityLabel)}</button><span>›</span>`)
      + `<span class="d3-here">${h.esc(item.label)}</span></nav>`;
    const steps = stepsOf(check);
    const values = valuesOf(check);
    const kvValues = check.values.filter((value) => !HIDDEN_VALUES.has(value.key)).map((value) => [value.key, h.esc(value.value)]);
    const allSpans = status ? status.levels.slice().reverse() : [];
    const spans = D3.allChanges ? allSpans : allSpans.slice(0, 12);
    const historyRows = spans.map((span) => `<div class="d3-ev"${PROBLEM.has(span.level) ? ' data-diagnostic' : ''}><span class="d3-t">${clock(span.from)}</span><span class="d3-evs">${h.shape(span.level, span.level || 'no data')}</span>`
      + `<span class="d3-evx">${span.level ? `${PROBLEM.has(span.level) ? `<span class="lvword lv-${h.esc(span.level)}">${h.esc(span.level)}</span> ` : 'OK · '}${h.esc(span.message || '')}${span.code ? ` <span class="d3-code">${h.esc(span.code)}</span>` : ''}` : '<span class="d3-quiet">no data</span>'}</span>`
      + `<span class="d3-age">${h.ago(span.to - span.from)}</span></div>`).join('');
    const caused = (check.caused_by || []).map((path) => `<a class="link" data-go="${h.esc(path)}">${h.esc((ctx.byPath.get(path) || {}).name || path)}</a>`);
    const meta = h.kv([
      ['level', `${h.shape(level, level)} ${h.esc(level)}${check.level !== level ? ` <span class="d3-quiet">(reported ${h.esc(check.level)})</span>` : ''}`],
      ['code', check.code ? `<span class="d3-code">${h.esc(check.code)}</span>` : '<span class="d3-quiet">none</span>'],
      ['source', h.esc(check.source || '—')],
      ['expected period', check.expected_period_s ? `every ${h.esc(check.expected_period_s)} s · stale after ${h.esc(check.stale_after_s)} s` : 'event-driven (never stale)'],
      ['last received', `${h.esc(check.received_iso || '—')} · ${h.ago(check.age_s)} ago`],
      check.hardware_id ? ['hardware id', `<span class="d3-code">${h.esc(check.hardware_id)}</span>`] : null,
      ['entity', item.entity === ctx.node.path ? h.esc(item.owner) : `<a class="link" data-go="${h.esc(item.entity)}">${h.esc(item.owner)}</a>`],
      caused.length ? ['caused by', listWords(caused)] : null,
    ]);
    const lead = `<p class="d3-lead"${PROBLEM.has(level) ? ' data-diagnostic' : ''}>${h.shape(level, level)}${PROBLEM.has(level) ? `<span class="lvword lv-${h.esc(level)}">${h.esc(level)}</span> ` : 'OK · '}${h.esc(check.message || '—')}</p>`;
    const heading = (text) => `<h4 class="d3-h">${text}</h4>`;
    const windowS = hist && hist.data ? hist.data.window_s : 900;
    return `<div class="d-head d3-head">${crumbs}<div class="d-title"><span>${h.esc(item.label)}</span></div></div><div class="d-body d3-page">`
      + lead
      + (steps.length ? `${heading('What to check')}<div class="d3-steps">${steps.map((line) => `<div>${h.esc(line)}</div>`).join('')}</div>`
        + (values.kernel_evidence ? `<div class="d3-meta">kernel evidence: ${h.esc(values.kernel_evidence)}</div>` : '') : '')
      + `${heading(`History · last ${h.esc(windowWords(windowS))}`)}`
      + (status ? `<div class="d3-barline"><span class="d3-quiet">${h.esc(windowWords(windowS))} ago</span>${strip(status.levels, from, to, 10)}<span class="d3-quiet">now</span></div><div class="d3-changes">${historyRows}</div>${allSpans.length > 12 ? `<button class="d3-plain d3-more" data-d3-allchanges>${D3.allChanges ? 'Show the latest 12' : `Show ${allSpans.length - 12} earlier`}</button>` : ''}`
        : `<div class="d3-quiet">${hist && hist.error ? `no history: ${h.esc(hist.error)}` : 'loading history…'}</div>`)
      + (check.measurements.length ? `${heading('Measurements')}<div class="d3-ms-list">${measurementRows(check, status, from, to, ctx, true)}</div>` : '')
      + (kvValues.length ? `${heading('Values')}${h.kv(kvValues)}` : '')
      + `${heading('About this diagnostic')}${meta}`
      + `<div class="d3-back"><button class="d3-plain link" data-d3-back>← back to ${h.esc(entityLabel)}</button></div></div>`;
  }

  // ------------------------------------------------------------------------------- the page ----
  function head(ctx, info) {
    const h = ctx.h;
    const node = ctx.node;
    const top = ctx.state && ctx.state.tree ? ctx.state.tree.path : 'host';
    const ancestors = (ctx.detail && ctx.detail.ancestors) || [];
    const parent = ancestors[ancestors.length - 1];
    const crumb = node.path === top ? '' : `<nav class="d3-crumb"><a class="link" data-go="${h.esc(top)}">Computer</a>`
      + (parent && parent.path !== top ? `<span>›</span><a class="link" data-go="${h.esc(parent.path)}">${h.esc(parent.name)}</a>` : '') + '</nav>';
    const mark = PROBLEM.has(node.level) ? h.shape(node.level, node.level) : (node.hint ? h.shape(node.hint.level, `below: ${node.hint.level}`) : '');
    return `<div class="d-head d3-head">${crumb}<div class="d-title">${h.icon(node.icon)}<span>${h.esc(info.label)}</span>${mark ? `<span class="d3-st${node.knock_on_of || (!PROBLEM.has(node.level) && node.hint) ? ' knock' : ''}">${mark}</span>` : ''}</div></div>`;
  }

  function renderGroup(ctx) {
    const h = ctx.h;
    const group = ctx.group;
    const members = group.children || [];
    const problems = members.filter((child) => child.status && (child.status.own || child.status.part || child.status.hint));
    const lead = problems.length
      ? `${problems.length} of ${members.length} need a look: ${listWords(problems.slice(0, 3).map((child) => `<b>${h.esc(child.label)}</b>`))}${problems.length > 3 ? ' and more' : ''}.`
      : `All ${members.length} report OK.`;
    const links = ctx.parts.links();
    ctx.draw(`${ctx.parts.head()}<div class="d-body d3-page"><p class="d3-lead">${lead}</p>${ctx.parts.members()}`
      + (links ? fold('links', 'Link health', links, { right: 'the computer diagnoses its own wires' }) : '') + '</div>');
  }

  function render(container, detail, ctx) {
    ensureStyle();
    wire(container);
    D3.last = { container, detail, ctx };
    if (ctx.group) { renderGroup(ctx); return; }
    if (!detail) {
      ctx.draw(`${ctx.parts.head()}<div class="d-body"><div class="d3-quiet">loading…</div></div>`);
      return;
    }
    const h = ctx.h;
    const node = ctx.node;
    const info = diagnostics(ctx);
    // history for the row and every part folded into it
    const hists = new Map([[node.path, history(node.path)]]);
    for (const part of info.parts) if (part.node && part.node.checks > 0) hists.set(part.entity, history(part.entity));
    const barEntry = bar(node.path);
    const windowS = (ctx.settings || {}).history_window_s || (hists.get(node.path).data || {}).window_s || 900;

    // the page's drill: a diagnostic of this row or of a part folded into it (ctx.diagnostic.path is its entity)
    const focus = ctx.diagnostic;
    if (focus) {
      const item = info.list.find((candidate) => candidate.entity === focus.path && candidate.check.name === focus.name);
      if (item) { ctx.draw(diagnosticPage(ctx, info, item, hists)); return; }
    }

    const inside = insideHtml(ctx, info, barEntry);
    const links = ctx.parts.links();
    const setup = ctx.parts.setup();
    const setupBusy = /s-panel/.test(setup);
    const details = [
      ctx.parts.identity() ? `<h4 class="d3-h">Identity</h4>${ctx.parts.identity()}` : '',
      setup ? `<h4 class="d3-h">Expected setup</h4><div class="sec setup">${setup}</div>` : '',
      ctx.parts.faults() ? `<h4 class="d3-h">Faults</h4>${ctx.parts.faults()}` : '',
      `<h4 class="d3-h">Events · this row and below</h4>${ctx.parts.events()}`,
    ].join('');
    ctx.draw(head(ctx, info) + `<div class="d-body d3-page">`
      + leadHtml(ctx, info)
      + tableHtml(ctx, info, hists)
      + (info.list.length || node.hint ? rollupLine(ctx, info) : '')
      + `<h4 class="d3-h">What changed · last ${h.esc(windowWords(windowS))}</h4>`
      + changedHtml(ctx, info, hists, barEntry, windowS)
      + `<div class="d3-folds">`
      + fold('inside', `Inside · ${inside.count}`, inside.html)
      + (links ? fold('links', 'Link health', links, { right: 'the computer diagnoses its own wires' }) : '')
      + fold('details', 'Details', details, { force: setupBusy, right: 'identity, expected setup, faults, events' })
      + '</div></div>');
  }

  // ------------------------------------------------------------------------------- clicks ----
  function wire(container) {
    if (container.__d3wired) return;
    container.__d3wired = true;
    container.addEventListener('click', (event) => {
      const t = event.target;
      const hit = t.closest('[data-d3-open], [data-d3-toggle], [data-d3-back], [data-d3-fold], [data-d3-allchanges]');
      if (!hit || !container.contains(hit) || t.closest('[data-go]') || !D3.last) return;
      event.stopPropagation();
      // a diagnostic's page and back are the page's drill (one browser-history entry each)
      if (hit.dataset.d3Open) {
        const [entity, name] = hit.dataset.d3Open.split('␟');
        D3.last.ctx.openDiagnostic(name, { path: entity });
        return;
      }
      if (hit.hasAttribute('data-d3-back')) { D3.last.ctx.closeDiagnostic(); return; }
      if (hit.dataset.d3Toggle) {
        const key = hit.dataset.d3Toggle;
        const shown = hit.closest('.d3-row') ? hit.closest('.d3-row').classList.contains('open') : false;
        if (hit.dataset.d3Force || !shown) { D3.open.add(key); D3.shut.delete(key); } else { D3.open.delete(key); D3.shut.add(key); }
      } else if (hit.dataset.d3Fold) {
        const id = hit.dataset.d3Fold;
        if (D3.folds.has(id)) D3.folds.delete(id); else D3.folds.add(id);
      } else if (hit.hasAttribute('data-d3-allchanges')) {
        D3.allChanges = !D3.allChanges;
      }
      D3.last.ctx.redraw();
    });
  }

  // ------------------------------------------------------------------------------- style ----
  // Tokens only; warn / error / stale / unknown fills only under [data-diagnostic] (the warn-colours rule).
  function ensureStyle() {
    if (typeof document === 'undefined' || document.head.querySelector('style[data-layout="d3"]')) return;
    const style = document.createElement('style');
    style.dataset.layout = 'd3';
    style.textContent = `
.d3-head { padding-bottom: 10px; }
.d3-crumb { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; font-size: 12px; color: var(--io-faint); margin-bottom: 6px; }
.d3-crumb .d3-here { color: var(--io-muted); }
.d3-head .d-title .d3-st { display: inline-flex; }
.d3-plain { background: transparent; border: 0; padding: 0; margin: 0; font: inherit; color: inherit; cursor: pointer; text-align: left; }
.d3-page { padding-top: 12px; }
.d3-lead { margin: 4px 0 14px; font-size: 14px; line-height: 1.55; color: var(--io-text-strong); }
.d3-lead .shape { margin-right: 6px; vertical-align: -2px; }
.d3-lead b { font-weight: 600; }
.d3-act { color: var(--io-accent-text); text-decoration: var(--io-link-decoration); }
.d3-act:hover { text-decoration: underline; }
.d3-quiet { color: var(--io-faint); font-size: 12px; }
.d3-nodata { color: var(--io-faint); font-size: 11px; }
.d3-table { border-top: 1px solid var(--io-border-soft); }
.d3-row { display: grid; grid-template-columns: 16px 18px minmax(96px, max-content) minmax(0, 1fr) auto; align-items: center; gap: 6px;
  min-height: var(--io-row-h); padding: 0 6px 0 2px; margin: 0 -6px 0 -2px; border-bottom: 1px solid var(--io-border-soft); cursor: pointer; border-radius: var(--io-radius-sm); }
.d3-row:hover { background: var(--io-hover); }
.d3-chev { display: inline-flex; color: var(--io-faint); }
.d3-chev .ic { width: 12px; height: 12px; transition: transform .12s; }
.d3-row.open .d3-chev .ic, .d3-fold-t[aria-expanded="true"] .ic { transform: rotate(90deg); }
.d3-st { display: inline-flex; justify-content: center; }
.d3-name { color: var(--io-text); max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.d3-name:hover { text-decoration: underline; color: var(--io-text-strong); }
.d3-row.prob .d3-name { color: var(--io-text-strong); font-weight: 600; }
.d3-msg { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-muted); font-size: 12.5px; }
.d3-row.prob .d3-msg { color: var(--io-text); }
.d3-row.open .d3-msg { white-space: normal; overflow-wrap: anywhere; padding: 4px 0; }
.d3-row.knock .d3-msg { color: var(--io-muted); }
.d3-age { color: var(--io-faint); font-size: 11px; font-family: var(--io-font-mono); white-space: nowrap; }
.d3-block { margin: 0 0 0 40px; padding: 8px 0 12px; border-bottom: 1px solid var(--io-border-soft); display: grid; gap: 8px; font-size: 12.5px; min-width: 0; }
.d3-p { color: var(--io-text); }
.d3-steps { display: grid; gap: 2px; color: var(--io-text); white-space: pre-line; }
.d3-meta { color: var(--io-faint); font-size: 11px; overflow-wrap: anywhere; }
.d3-meta .link { font-size: 11px; }
.d3-ms-list { display: grid; gap: 2px; }
.d3-m { display: grid; grid-template-columns: minmax(90px, 32%) minmax(0, 1fr) minmax(70px, auto); align-items: center; gap: 8px; min-height: 20px; }
.d3-m.big { grid-template-columns: 1fr auto; grid-template-areas: "k v" "s s"; padding: 4px 0 8px; border-bottom: 1px solid var(--io-border-soft); }
.d3-m.big .d3-mk { grid-area: k; } .d3-m.big .d3-mv { grid-area: v; } .d3-m.big .d3-ms { grid-area: s; }
.d3-mk { color: var(--io-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.d3-mv { color: var(--io-text); font-family: var(--io-font-mono); font-size: 11.5px; text-align: right; white-space: nowrap; display: inline-flex; gap: 4px; align-items: center; justify-content: flex-end; }
.d3-mv .shape { width: 12px; height: 12px; }
.d3-ms { min-width: 0; display: flex; align-items: center; }
.d3-spark { display: block; overflow: visible; }
.d3-spark .d3-line { fill: none; stroke: var(--io-muted); stroke-width: 1.3; vector-effect: non-scaling-stroke; stroke-linejoin: round; }
.d3-spark .d3-dot { fill: var(--io-muted); }
.d3-spark .d3-limit { stroke: var(--io-border); stroke-width: 1; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
.d3-spark.big { background: var(--io-surface); border-bottom: 1px solid var(--io-border-soft); }
[data-diagnostic] .d3-spark .d3-line { stroke: var(--io-text-strong); }
.d3-cores { display: flex; align-items: flex-end; gap: 1px; height: 18px; min-width: 0; }
.d3-cores i { flex: 1; min-width: 2px; max-width: 8px; background: var(--io-faint); opacity: .75; border-radius: 1px 1px 0 0; }
.d3-trend { max-width: 100%; }
.d3-strip { flex: 1; min-width: 0; }
.d3-strip svg { display: block; }
.d3-base { stroke: var(--io-border-soft); stroke-width: 1; vector-effect: non-scaling-stroke; }
.d3-lv-ok { fill: color-mix(in srgb, var(--io-faint) 40%, transparent); background: color-mix(in srgb, var(--io-faint) 40%, transparent); }
.d3-lv-warn { fill: var(--io-warn); background: var(--io-warn); }
.d3-lv-error { fill: var(--io-error); background: var(--io-error); }
.d3-lv-stale { fill: var(--io-stale); background: var(--io-stale); }
.d3-lv-unknown { fill: var(--io-unknown); background: var(--io-unknown); }
.d3-rollup { margin: 8px 0 4px; color: var(--io-faint); font-size: 11.5px; line-height: 1.5; }
.d3-h { margin: 18px 0 6px; font: 600 12px/1.3 var(--io-font-ui); color: var(--io-muted); text-transform: none; letter-spacing: 0; }
.d3-barline { display: flex; align-items: center; gap: 8px; margin: 2px 0 8px; }
.d3-barline > .ioh { flex: 1 1 auto; }
.d3-changes { display: grid; }
.d3-ev { display: grid; grid-template-columns: 62px 16px minmax(0, 1fr) auto; gap: 6px; align-items: baseline; padding: 3px 0; font-size: 12.5px; color: var(--io-text); border-bottom: 1px solid var(--io-border-soft); }
.d3-ev:last-child { border-bottom: 0; }
.d3-t { color: var(--io-faint); font-family: var(--io-font-mono); font-size: 11.5px; }
.d3-evs .shape { width: 12px; height: 12px; }
.d3-evx { min-width: 0; overflow-wrap: anywhere; }
.d3-evx b { font-weight: 600; color: var(--io-text-strong); }
.d3-evx .lvword, .d3-lead .lvword, .d3-msg .lvword { margin-right: 0; }
.d3-msg .lvword { margin-right: 6px; }
.d3-range { color: var(--io-faint); font-family: var(--io-font-ui); font-size: 11px; margin-right: 8px; }
.d3-code { font-family: var(--io-font-mono); font-size: 11.5px; color: var(--io-muted); }
.d3-more { margin-top: 4px; color: var(--io-muted); font-size: 12px; }
.d3-more:hover { color: var(--io-text); text-decoration: underline; }
.d3-folds { margin-top: 18px; border-top: 1px solid var(--io-border-soft); }
.d3-fold { border-bottom: 1px solid var(--io-border-soft); }
.d3-fold-t { display: flex; align-items: center; gap: 6px; width: 100%; padding: 7px 0; color: var(--io-text); font-size: 13px; }
.d3-fold-t .ic { width: 12px; height: 12px; color: var(--io-faint); transition: transform .12s; }
.d3-fold-t > span:not(.d3-quiet) { flex: none; }
.d3-fold-t .d3-quiet { margin-left: auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.d3-fold-t:hover { color: var(--io-text-strong); }
.d3-fold-b { padding: 0 0 12px 18px; min-width: 0; }
.d3-fold-b .d3-h:first-child { margin-top: 2px; }
.d3-child { display: grid; grid-template-columns: 16px minmax(80px, max-content) minmax(0, 1fr) minmax(60px, 28%) 18px; gap: 8px; align-items: center; min-height: var(--io-row-h); padding: 0 6px; margin: 0 -6px; border-radius: var(--io-radius-sm); cursor: pointer; }
.d3-child:hover { background: var(--io-hover); }
.d3-child .ic { color: var(--io-icon); }
.d3-cn { color: var(--io-text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 240px; }
.d3-cm { color: var(--io-muted); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.d3-cb .ioh { width: 100%; }
.d3-back { margin-top: 20px; font-size: 12.5px; }
`;
    document.head.appendChild(style);
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d3 = {
    name: 'Readable summary',
    description: 'A Notion-like page: one plain sentence of what is wrong and what to do, the status table (each row opens in place: values, sparklines, what to check; its title opens its own page), What changed in the history window as sentences, children; the utilities are in the header.',
    order: 3,
    render,
  };
})(typeof window !== 'undefined' ? window : globalThis);
