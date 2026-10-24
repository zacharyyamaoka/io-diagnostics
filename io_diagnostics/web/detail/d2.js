// Detail layout d2 · State timeline (round 3 proposal; contract: docs/round2-seams.md §6).
// Grafana's state-timeline panel / a logic analyser: every diagnostic of the entity is one LANE on one shared time
// axis spanning the app-wide history window (/api/v1/history level spans, /api/v1/history/bar for the entity's own
// rollup and its children). To the right of each lane sit its current level and message, so the lanes read as the
// status table first (Zach, 2026-10-23: "the first thing you see on the entity is the table that shows the different
// diagnostics, their current status and the message"). Hovering the axis scrubs every lane together; clicking a lane
// opens that diagnostic's page, which puts its measurement trends on the same axis. The Computer's CPU, RAM and
// temperature measurements are small line lanes under their status lanes. The utilities are the page's, in the entity
// header's Utilities ▾ (round 3.5). Round 3.5 seams: the history is the page's shared cache (ctx.history, ctx.bar),
// the diagnostic page is the page's drill (ctx.diagnostic, ctx.openDiagnostic / closeDiagnostic: back / forward work).
'use strict';
(function (root) {
  const usbText = (text) => (root.IOUsb ? root.IOUsb.label(text) : String(text ?? '')); // a USB link is named by web/usb.js, never "480 Mb/s"
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const TRACK_W = 300;         // a lane's track, in px: the cell count follows it (health.js)
  const BINS = 120;            // health-bar bins per lane (the bar API's own rule decides each bin's level)
  const REFRESH_MS = 2500;     // /api/v1/history and /history/bar while the entity is shown (the API asks 2-5 s)
  const MAX_TICKS = 160;       // past this many arrivals in the window the ticks are a smear: drawn as none
  const QUIET_TICKS = 16;      // on the entity's lanes, only sparse reports get ticks (a 10 s period is a dotted rule)

  // ------------------------------------------------------------------------------------------ style ----
  // Tokens only. WHY height as well as colour: ISA-101 alarm priority by shape + position, and a logic analyser's
  // low/high trace: OK is a thin quiet baseline, a warning half height, an error or a stale span full height.
  const CSS = `
.d2-tl { container-type: inline-size; margin: 2px 0 4px; }
.d2-lane { display: grid; grid-template-columns: 30% minmax(0, 1fr) 34%; grid-template-areas: "name track read"; column-gap: 10px; align-items: stretch; min-height: 24px; border-radius: var(--io-radius-sm); }
.d2-lane > .d2-name { grid-area: name; display: flex; align-items: center; min-width: 0; gap: 4px; font-size: 12.5px; color: var(--io-text); white-space: nowrap; padding-left: 4px; }
.d2-lane > .d2-track { grid-area: track; position: relative; min-height: 24px; }
.d2-lane > .d2-read { grid-area: read; display: flex; align-items: center; min-width: 0; gap: 6px; font-size: 12.5px; color: var(--io-muted); white-space: nowrap; }
.d2-name .d2-pre { min-width: 2.4em; overflow: hidden; text-overflow: ellipsis; color: var(--io-faint); flex: 0 12 auto; }
.d2-name .d2-key { min-width: 0; overflow: hidden; text-overflow: ellipsis; flex: 0 1 auto; }
.d2-read .d2-msg { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.d2-read .d2-age, .d2-read .d2-when { color: var(--io-faint); font-size: 11px; flex: none; font-family: var(--io-font-mono); }
.d2-read .shape { width: 12px; height: 12px; }
.d2-lane[data-d2-diag], .d2-lane[data-go] { cursor: pointer; }
.d2-lane[data-d2-diag]:hover, .d2-lane[data-go]:hover, .d2-lane[data-d2-diag]:focus-visible { background: var(--io-hover); outline: none; }
.d2-lane[data-d2-diag]:focus-visible { box-shadow: inset 0 0 0 var(--io-focus-width) var(--io-focus); }
.d2-lane.d2-roll > .d2-name { color: var(--io-text-strong); font-weight: 600; }
.d2-lane.d2-head > .d2-name { font-size: 11px; text-transform: uppercase; letter-spacing: .6px; color: var(--io-muted); font-weight: 600; }
.d2-lane.d2-head { min-height: 26px; margin-top: 6px; border-top: 1px solid var(--io-border-soft); border-radius: 0; }
.d2-lane.d2-head > .d2-read { font-size: 11.5px; color: var(--io-faint); }
.d2-lane.d2-sub { min-height: 22px; }
.d2-lane.d2-sub > .d2-name { padding-left: 18px; font-size: 11.5px; color: var(--io-muted); }
.d2-lane.d2-sub > .d2-read { font-size: 11.5px; font-family: var(--io-font-mono); }
.d2-lane.d2-axis { min-height: 20px; }
.d2-lane.d2-axis > .d2-name, .d2-lane.d2-axis > .d2-read { font-size: 11px; color: var(--io-faint); }
.d2-axis .d2-lbl { position: absolute; bottom: 3px; transform: translateX(-50%); font-size: 10.5px; color: var(--io-faint); font-family: var(--io-font-mono); white-space: nowrap; }
.d2-axis .d2-lbl.d2-l0 { transform: none; }
.d2-axis .d2-lbl.d2-l1 { transform: translateX(-100%); }
.d2-axis .d2-grid { position: absolute; bottom: 0; width: 1px; height: 3px; background: var(--io-border); }
.d2-axis .d2-curlbl { position: absolute; top: 0; transform: translateX(-50%); font-size: 10.5px; padding: 0 4px; border-radius: var(--io-radius-sm); background: var(--io-popover); color: var(--io-text-strong); font-family: var(--io-font-mono); white-space: nowrap; box-shadow: 0 0 0 1px var(--io-border); z-index: 2; }
.d2-track.d2-nodata { background-image: linear-gradient(to right, var(--io-border-soft) 50%, transparent 0); background-size: 4px 1px; background-repeat: repeat-x; background-position: left calc(100% - 5px); }
.d2-track > .ioh.d2-cells { position: absolute; left: 0; right: 0; top: 3px; bottom: 3px; height: auto; }
.d2-tick { position: absolute; bottom: 0; width: 1px; height: 3px; background: var(--io-faint); }
.d2-cur { position: absolute; top: 0; bottom: 0; width: 1px; margin-left: -.5px; background: var(--io-text-strong); pointer-events: none; z-index: 1; }
.d2-spark { position: absolute; left: 0; top: 3px; width: 100%; height: calc(100% - 6px); overflow: visible; }
.d2-spark path { fill: none; stroke: var(--io-muted); stroke-width: 1.25; vector-effect: non-scaling-stroke; }
.d2-spark path.d2-faint { stroke: var(--io-guide-strong); stroke-width: 1; }
.d2-spark line { stroke: var(--io-faint); stroke-width: 1; stroke-dasharray: 3 3; vector-effect: non-scaling-stroke; }
.d2-rolls { font-size: 12.5px; color: var(--io-muted); margin: 10px 0 2px; display: flex; flex-wrap: wrap; align-items: center; gap: 4px 6px; }
.d2-rolls b { color: var(--io-text-strong); font-weight: 600; }
.d2-crumb { font-size: 12px; margin-bottom: 6px; }
.d2-hist { width: 100%; border-collapse: collapse; font-size: 12px; table-layout: fixed; }
.d2-hist td { padding: 3px 6px 3px 0; border-bottom: 1px solid var(--io-border-soft); vertical-align: top; overflow-wrap: anywhere; }
.d2-hist td.d2-t { width: 132px; font-family: var(--io-font-mono); color: var(--io-faint); font-size: 11px; white-space: nowrap; }
.d2-hist td.d2-s { width: 18px; }
.d2-hist td.d2-c { width: 110px; font-family: var(--io-font-mono); color: var(--io-faint); font-size: 11px; }
.d2-hist tr.d2-none td { color: var(--io-faint); }
.d2-hist .shape { width: 12px; height: 12px; }
.d2-note { font-size: 11.5px; color: var(--io-faint); margin: 2px 0 6px; }
@container (max-width: 520px) {
  .d2-lane { grid-template-columns: minmax(0, 1fr) minmax(0, 1.25fr); grid-template-areas: "name read" "track track"; }
  .d2-lane > .d2-track { min-height: 20px; }
  .d2-lane.d2-axis { grid-template-areas: "track track"; }
  .d2-lane.d2-axis > .d2-name, .d2-lane.d2-axis > .d2-read { display: none; }
  .d2-lane.d2-head > .d2-track { display: none; }
}`;
  function injectStyle() {
    if (typeof document === 'undefined' || document.head.querySelector('style[data-layout="d2"]')) return;
    const style = document.createElement('style');
    style.dataset.layout = 'd2';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  // ------------------------------------------------------------------------------------------- state ----
  let hoverT = null;           // the scrubbed instant (host clock, s), or null
  let last = null;             // the last render's arguments, to redraw on hover

  // the page's shared history cache: an entry {data, error, loading, at}; an answer that lands redraws the detail
  const want = (kind, path) => (kind === 'hist' ? last.ctx.history(path, { everyMs: REFRESH_MS }) : last.ctx.bar(path, BINS, { everyMs: REFRESH_MS }));
  const got = want;

  function redraw() {
    if (!last || typeof document === 'undefined') return;
    if (!last.container.querySelector('.d2-root')) return; // another layout is drawing the panel now
    last.ctx.redraw();
  }

  // ------------------------------------------------------------------------------------------ helpers ----
  const pad2 = (n) => String(n).padStart(2, '0');
  const clock = (t) => { const d = new Date(t * 1000); return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`; };
  const dur = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${+(s / 60).toFixed(s % 60 ? 1 : 0)} min` : `${+(s / 3600).toFixed(1)} h`);
  const pct = (t, axis) => Math.max(0, Math.min(100, ((t - axis.from) / (axis.to - axis.from)) * 100));
  // the stock rendering (contract Measurement.render): "%g" + the UCUM unit, nothing renamed
  function valueText(value, unit) {
    let text;
    if (value === null || value === undefined) text = 'unavailable';
    else if (typeof value === 'boolean') text = value ? 'true' : 'false';
    else if (Number.isInteger(value)) text = String(value);
    else text = String(Number(Number(value).toPrecision(6)));
    return unit && unit !== '1' ? `${text} ${unit}` : text;
  }
  // "28B3A25D CAN bus: bus" -> the prefix (the entity, faint, shrinks first) and the check's own words; both shown
  function nameHtml(h, name) {
    const at = name.lastIndexOf(': ');
    if (at <= 0) return `<span class="d2-key">${h.esc(name)}</span>`;
    return `<span class="d2-pre">${h.esc(name.slice(0, at + 1))}</span><span class="d2-key">${h.esc(name.slice(at + 2))}</span>`;
  }
  const spanAt = (levels, t) => (levels || []).find((span) => span.from <= t && t < span.to) || ((levels || []).length && t >= levels[levels.length - 1].to - 1e-6 ? levels[levels.length - 1] : null);
  // Where reports arrived: every measurement sample's receive stamp, and the start of each reported span (a span that
  // starts at a promise running out is stale; no data is nobody). Between two ticks the level is HELD, not re-reported.
  function arrivals(status) {
    const stamps = new Set();
    for (const series of status.measurements || []) for (const [t] of series.points) stamps.add(+t.toFixed(3));
    for (const span of status.levels || []) if (span.level && span.level !== 'stale') stamps.add(+span.from.toFixed(3));
    return [...stamps].sort((a, b) => a - b);
  }

  // ------------------------------------------------------------------------------------- lane pieces ----
  function cursor(axis) {
    return hoverT !== null && axis ? `<i class="d2-cur" style="left:${pct(hoverT, axis).toFixed(3)}%"></i>` : '';
  }
  function spanTrack(status, axis, opts = {}) {
    if (!axis || !status) return `<div class="d2-track d2-nodata">${cursor(axis)}</div>`;
    // the shared discrete state timeline (detail/health.js), the same axis: separate tall cells, hollow = no data
    const segs = IOHealth.html(IOHealth.fromLevels(status.levels || [], axis.from, axis.to, IOHealth.count(axis.to - axis.from, TRACK_W, 'lane')), { kind: 'lane', cls: 'd2-cells' });
    const stamps = opts.ticks === false ? [] : arrivals(status).filter((t) => t >= axis.from && t <= axis.to);
    const ticks = stamps.length <= (opts.maxTicks || QUIET_TICKS) ? stamps.map((t) => `<i class="d2-tick" style="left:${pct(t, axis).toFixed(3)}%"></i>`).join('') : '';
    return `<div class="d2-track d2-nodata">${segs}${ticks}${cursor(axis)}</div>`;
  }
  // a health bar (the bar API's bins: the worst level any status held in each) as merged segments on the same axis
  function barTrack(bar, rowData, axis) {
    if (!axis || !bar || !rowData) return `<div class="d2-track d2-nodata">${cursor(axis)}</div>`;
    const html = IOHealth.html(IOHealth.rebin(IOHealth.fromBar(rowData.bar, bar.from, bar.bin_s), IOHealth.count(axis.to - axis.from, TRACK_W, 'lane')), { kind: 'lane', cls: 'd2-cells' });
    return `<div class="d2-track d2-nodata">${html}${cursor(axis)}</div>`;
  }
  const binAt = (bar, rowData, t) => {
    if (!bar || !rowData) return undefined;
    const at = Math.floor((t - bar.from) / bar.bin_s);
    return at >= 0 && at < rowData.bar.length ? { level: rowData.bar[at], from: bar.from + at * bar.bin_s, to: bar.from + (at + 1) * bar.bin_s } : undefined;
  };

  // A measurement series drawn as a held step line (each sample holds until the next one or until it would go stale:
  // never a line through a gap the producer did not fill). Bools step between 0 and 1.
  function seriesValueAt(series, t, holdS) {
    let found = null;
    for (const point of series.points) { if (point[0] <= t) found = point; else break; }
    if (!found) return { state: 'none' };
    if (holdS && t - found[0] > holdS) return { state: 'stale', point: found };
    return { state: 'value', point: found };
  }
  function rangeOf(seriesList) {
    const values = seriesList.flatMap((series) => series.points.map((point) => point[1])).filter((v) => typeof v === 'number' && Number.isFinite(v));
    if (!values.length) return null;
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    if (seriesList.every((series) => series.unit === '%')) { lo = Math.min(0, lo); hi = Math.max(100, hi); }
    // WHY a floor on the span: autoscaling a 48-49 °C wobble to full height reads as a swing that is not there
    const floor = Math.max(Math.max(Math.abs(lo), Math.abs(hi)) * 0.1, seriesList.every((series) => series.unit === 'Cel') ? 10 : 0, 1e-6);
    if (hi - lo < floor) { const mid = (hi + lo) / 2; lo = mid - floor / 2; hi = mid + floor / 2; }
    const padding = (hi - lo) * 0.08;
    return [lo - padding, hi + padding];
  }
  function sparkTrack(seriesList, axis, holdS, faint = []) {
    const all = [...seriesList, ...faint];
    const range = rangeOf(all.filter((series) => series.points.some((point) => typeof point[1] === 'number')))
      || (all.some((series) => series.points.some((point) => typeof point[1] === 'boolean')) ? [-0.1, 1.1] : null);
    if (!axis || !range) return `<div class="d2-track d2-nodata">${cursor(axis)}</div>`;
    const H = 20;
    const X = (t) => ((t - axis.from) / (axis.to - axis.from)) * 1000;
    const Y = (v) => (H - ((v - range[0]) / (range[1] - range[0])) * H);
    const num = (v) => (typeof v === 'boolean' ? (v ? 1 : 0) : v);
    const pathOf = (series) => {
      let d = '';
      const points = series.points.filter((point) => point[0] <= axis.to);
      points.forEach((point, at) => {
        const v = num(point[1]);
        if (typeof v !== 'number' || !Number.isFinite(v)) return;
        const next = points[at + 1];
        const until = Math.min(next ? next[0] : axis.to, holdS ? point[0] + holdS : Infinity, axis.to);
        if (until < axis.from) return;
        const x0 = Math.max(0, X(point[0])).toFixed(1);
        const x1 = X(until).toFixed(1);
        const y = Y(v).toFixed(2);
        const joined = d && at > 0 && Number.isFinite(num(points[at - 1][1])) && (!holdS || point[0] - points[at - 1][0] <= holdS) && X(points[at - 1][0]) >= 0;
        d += joined ? `V${y}H${x1}` : `M${x0} ${y}H${x1}`;
      });
      return d;
    };
    const limits = seriesList.flatMap((series) => [series.warn_high, series.warn_low, series.error_high, series.error_low])
      .filter((v) => typeof v === 'number' && v > range[0] && v < range[1]);
    const lines = [...new Set(limits)].map((v) => `<line x1="0" x2="1000" y1="${Y(v).toFixed(2)}" y2="${Y(v).toFixed(2)}"/>`).join('');
    const paths = faint.map((series) => { const d = pathOf(series); return d ? `<path class="d2-faint" d="${d}"/>` : ''; }).join('')
      + seriesList.map((series) => { const d = pathOf(series); return d ? `<path d="${d}"/>` : ''; }).join('');
    return `<div class="d2-track"><svg class="d2-spark" viewBox="0 0 1000 ${H}" preserveAspectRatio="none" aria-hidden="true">${lines}${paths}</svg>${cursor(axis)}</div>`;
  }

  // the Computer's line lanes (Zach's ROS2 System Webview reference): CPU, RAM and swap, every temperature sensor
  function computerSubLanes(status) {
    const series = status.measurements || [];
    const by = (key) => series.filter((m) => m.key === key);
    const name = status.name;
    if (/CPU Information$/.test(name)) {
      const cores = series.filter((m) => /^CPU \d+ Load$/.test(m.key));
      return [{ label: 'CPU load', main: by('CPU Load Average'), faint: cores, note: cores.length ? `${cores.length} cores` : '' }];
    }
    if (/RAM Information$/.test(name)) return [{ label: 'RAM', main: by('RAM Load Average'), faint: [] }, { label: 'swap', main: by('swap used %'), faint: [] }];
    const temps = series.filter((m) => m.unit === 'Cel');
    if (temps.length) return [{ label: temps.length > 1 ? `temperature · ${temps.length} sensors` : 'temperature', main: temps, faint: [], hottest: true }];
    return [];
  }
  function subReadout(lane, t, holdS, h) {
    const pick = (list) => {
      const read = list.map((series) => ({ series, at: t === null ? seriesValueAt(series, Infinity, null) : seriesValueAt(series, t, holdS) }))
        .filter((item) => item.at.state === 'value');
      if (!read.length) return null;
      return lane.hottest ? read.reduce((a, b) => (Number(b.at.point[1]) > Number(a.at.point[1]) ? b : a)) : read[0];
    };
    const main = pick(lane.main);
    if (!main) return '<span class="d2-msg">no data</span>';
    const value = valueText(main.at.point[1], main.series.unit);
    const who = lane.hottest && lane.main.length > 1 ? `${main.series.key} ` : '';
    const coreMax = lane.faint.length ? lane.faint.map((series) => (t === null ? seriesValueAt(series, Infinity, null) : seriesValueAt(series, t, holdS)))
      .filter((at) => at.state === 'value').reduce((max, at) => Math.max(max, Number(at.point[1])), -Infinity) : -Infinity;
    return `<span class="d2-msg" title="${h.esc(`${who}${value}`)}">${h.esc(who)}${h.esc(value)}${Number.isFinite(coreMax) ? ` · busiest core ${h.esc(valueText(coreMax, '%'))}` : ''}</span>`
      + (lane.note && t === null ? `<span class="d2-age">${h.esc(lane.note)}</span>` : '');
  }

  // the time axis: nice relative ticks ("−10 min" … "now"), and the scrubbed instant's clock time
  function axisRow(axis, h, opts = {}) {
    const name = `<div class="d2-name">${opts.name || ''}</div>`;
    if (!axis) return `<div class="d2-lane d2-axis">${name}<div class="d2-track"><span class="d2-lbl d2-l0">${h.esc(opts.empty || 'loading history…')}</span></div><div class="d2-read"></div></div>`;
    const span = axis.to - axis.from;
    const step = [10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200].find((s) => span / s <= 4.2) || 43200;
    let labels = '';
    for (let k = 0; k * step <= span + 1e-6; k += 1) {
      const t = axis.to - k * step;
      const left = pct(t, axis);
      const cls = k === 0 ? ' d2-l1' : left < 6 ? ' d2-l0' : '';
      labels += `<i class="d2-grid" style="left:${left.toFixed(3)}%"></i>`;
      if (hoverT === null && (k === 0 || left > 8)) labels += `<span class="d2-lbl${cls}" style="left:${left.toFixed(3)}%">${k === 0 ? 'now' : `−${h.esc(dur(k * step))}`}</span>`;
    }
    const cur = hoverT !== null ? `<span class="d2-curlbl" style="left:${pct(hoverT, axis).toFixed(3)}%">${clock(hoverT)}</span>` : '';
    const read = hoverT !== null ? `at ${clock(hoverT)} · ${h.esc(dur(Math.max(0, axis.to - hoverT)))} before ${clock(axis.to)}`
      : `last ${h.esc(dur(span))} · as of ${clock(axis.to)}`;
    return `<div class="d2-lane d2-axis">${name}<div class="d2-track">${labels}${cur}${cursor(axis)}</div><div class="d2-read"><span class="d2-msg">${read}</span></div></div>`;
  }
  // the readout of a span lane: at rest the current report; scrubbed, the held level at that instant
  function spanReadout(status, check, h) {
    if (hoverT !== null && status) {
      const span = spanAt(status.levels, hoverT);
      if (!span || !span.level) return '<span class="d2-msg">no data</span>';
      const reported = arrivals(status).filter((t) => t <= hoverT).pop();
      const when = span.level === 'stale' ? `since ${clock(span.from)}` : reported !== undefined ? `reported ${clock(reported)}` : `since ${clock(span.from)}`;
      return `${h.shape(span.level)}<span class="d2-msg" title="${h.esc(span.message)}">${h.esc(span.message || span.level)}</span><span class="d2-when">${h.esc(when)}</span>`;
    }
    if (!check) return '<span class="d2-msg">no current report</span>';
    return `${h.shape(check.effective_level)}<span class="d2-msg" title="${h.esc(check.message)}">${h.esc(usbText(check.message || '—'))}</span><span class="d2-age">${h.esc(h.ago(check.age_s))}</span>`;
  }

  // ------------------------------------------------------------------------------- the entity's lanes ----
  function entityAxis(hist, bar) {
    if (hist && hist.data) return { from: hist.data.from, to: hist.data.to, window: hist.data.window_s };
    if (bar && bar.data) return { from: bar.data.from, to: bar.data.to, window: bar.data.window_s };
    return null;
  }
  // own statuses in the API's order (worst first), then any the history still holds that no longer report
  function laneStatuses(detail, hist) {
    const byName = new Map(((hist && hist.statuses) || []).map((status) => [status.name, status]));
    const rows = detail.checks.map((check) => ({ name: check.name, check, status: byName.get(check.name) || null }));
    const seen = new Set(rows.map((row) => row.name));
    for (const status of (hist && hist.statuses) || []) if (!seen.has(status.name)) rows.push({ name: status.name, check: null, status });
    return rows;
  }
  function rollsUp(ctx, detail, h) {
    const node = ctx.node;
    const bits = [];
    const worst = detail.checks.filter((check) => PROBLEM.has(check.effective_level))
      .sort((a, b) => RANK[b.effective_level] - RANK[a.effective_level])[0];
    if (worst) {
      bits.push(`${h.shape(worst.effective_level)}<span>reads <b>${h.esc(worst.effective_level)}</b> from</span><a class="link" data-d2-diag="${h.esc(worst.name)}">${h.esc(worst.name)}</a>`);
      const causes = (worst.caused_by || []).map((path) => `<a class="link" data-go="${h.esc(path)}">${h.esc((ctx.byPath.get(path) || {}).name || path)}</a>`);
      if (causes.length) bits.push(`<span>caused by</span>${causes.join('<span>·</span>')}`);
    } else if (node.own && node.own.level === null) {
      bits.push('<span>no diagnostics of its own: it groups what is below</span>');
    } else {
      bits.push(`<span>${detail.checks.length} diagnostic${detail.checks.length === 1 ? '' : 's'}, all OK</span>`);
    }
    if (node.connected === false) bits.push('<span>· not connected</span>');
    if (node.knock_on_of) bits.push(`<span>· explained by</span><a class="link" data-go="${h.esc(node.knock_on_of)}">${h.esc((ctx.byPath.get(node.knock_on_of) || {}).name || node.knock_on_of)}</a>`);
    if (node.hint) bits.push(`<span>· below:</span><a class="link" data-go="${h.esc(node.hint.origin)}">${h.esc(usbText(node.rollup.origin_name))}</a><span>is ${h.esc(node.hint.level)}</span>`);
    return `<div class="d2-rolls" data-diagnostic>${bits.join('')}</div>`;
  }
  function entityTimeline(ctx, detail) {
    const h = ctx.h;
    const node = ctx.node;
    const path = node.path;
    const hist = want('hist', path);
    const bar = want('bar', path);
    const axis = entityAxis(hist, bar);
    const histData = hist.data && hist.data.path === path ? hist.data : null;
    const barData = bar.data && bar.data.path === path ? bar.data : null;
    const rows = barData ? barData.rows : [];
    const own = rows.find((row) => row.depth === 0);
    const empty = hist.error ? `no history (${hist.error})` : 'loading history…';
    const label = ctx.row ? ctx.row.label : usbText(node.name);
    let html = axisRow(axis, h, { name: '<span class="d2-key">diagnostic</span>', empty });
    // the entity's own rollup: the worst level any of its own statuses held in each bin
    const rollAt = hoverT !== null ? binAt(barData, own, hoverT) : undefined;
    const rollRead = hoverT !== null
      ? (rollAt && rollAt.level ? `${h.shape(rollAt.level)}<span class="d2-msg">worst ${h.esc(rollAt.level)}</span><span class="d2-when">${clock(rollAt.from)}–${clock(rollAt.to)}</span>` : '<span class="d2-msg">no data</span>')
      : `${h.shape(node.level)}<span class="d2-msg" title="${h.esc(node.message)}">${h.esc(node.message || (node.own.level === null ? 'grouping: nothing of its own' : '—'))}</span>`;
    html += `<div class="d2-lane d2-roll" data-diagnostic title="${h.esc(own && own.basis === 'below' ? 'the worst level reported at or below it, per bin' : 'the worst level its own diagnostics held, per bin')}">`
      + `<div class="d2-name"><span class="d2-key">${h.esc(label)}</span></div>${barTrack(barData, own, axis)}<div class="d2-read">${rollRead}</div></div>`;
    // one lane per diagnostic, its line lanes under it on the Computer
    for (const lane of laneStatuses(detail, histData)) {
      const holdS = lane.status ? lane.status.stale_after_s : null;
      html += `<div class="d2-lane" data-diagnostic data-d2-diag="${h.esc(lane.name)}" role="button" tabindex="0" aria-label="${h.esc(`${lane.name}: open its page`)}">`
        + `<div class="d2-name">${nameHtml(h, lane.name)}</div>${spanTrack(lane.status, axis)}<div class="d2-read">${spanReadout(lane.status, lane.check, h)}</div></div>`;
      if (ctx.isComputer && lane.status) {
        for (const sub of computerSubLanes(lane.status)) {
          if (!sub.main.length) continue;
          html += `<div class="d2-lane d2-sub" data-diagnostic data-d2-diag="${h.esc(lane.name)}">`
            + `<div class="d2-name"><span class="d2-key" title="${h.esc(sub.main.map((m) => m.key).join(', '))}">${h.esc(sub.label)}</span></div>${sparkTrack(sub.main, axis, holdS, sub.faint)}`
            + `<div class="d2-read">${subReadout(sub, hoverT, holdS, h)}</div></div>`;
        }
      }
    }
    // children: their health bars under one header row
    const children = barData ? rows.filter((row) => row.depth > 0)
      : ((detail.node.children || []).map((child) => ({ path: child, name: (ctx.byPath.get(child) || {}).name || child, bar: null })));
    if (children.length) {
      const hint = node.hint ? `${h.shape(node.hint.level)}<span class="d2-msg">${h.esc(usbText(node.rollup.origin_name))} is ${h.esc(node.hint.level)}</span>` : '<span class="d2-msg">click one to open it</span>';
      html += `<div class="d2-lane d2-head" data-diagnostic><div class="d2-name">Children · ${children.length}</div><div class="d2-track">${cursor(axis)}</div><div class="d2-read">${hint}</div></div>`;
      for (const child of children) {
        const childNode = ctx.byPath.get(child.path) || {};
        const at = hoverT !== null ? binAt(barData, child, hoverT) : undefined;
        const read = hoverT !== null
          ? (at && at.level ? `${h.shape(at.level)}<span class="d2-msg">worst ${h.esc(at.level)}</span>` : '<span class="d2-msg">no data</span>')
          : `<span class="${childNode.knock_on_of ? 'knock' : ''}">${h.shape(childNode.level)}</span><span class="d2-msg" title="${h.esc(childNode.message || '')}">${h.esc(childNode.message || (child.basis === 'none' ? 'nothing reports' : '—'))}</span>`;
        html += `<div class="d2-lane" data-diagnostic data-go="${h.esc(child.path)}">`
          + `<div class="d2-name"><span class="d2-key">${h.esc(usbText(child.name))}</span></div>${barTrack(barData, child.bar ? child : null, axis)}<div class="d2-read">${read}</div></div>`;
      }
    }
    return `<div class="d2-tl">${html}</div>`;
  }

  // ------------------------------------------------------------------------------- a diagnostic's page ----
  function diagnosticPage(ctx, detail, name) {
    const h = ctx.h;
    const s = ctx.section;
    const node = ctx.node;
    const hist = want('hist', node.path);
    const histData = hist.data && hist.data.path === node.path ? hist.data : null;
    const status = histData ? histData.statuses.find((item) => item.name === name) || null : null;
    const check = detail.checks.find((item) => item.name === name) || null;
    const axis = entityAxis(hist, null);
    const label = ctx.row ? ctx.row.label : usbText(node.name);
    const level = check ? check.effective_level : (status && status.levels.length ? status.levels[status.levels.length - 1].level : null);
    const head = `<div class="d-head"><div class="d2-crumb"><a class="link" data-d2-back title="back to ${h.esc(label)}">← ${h.esc(label)}</a></div>`
      + `<div class="d-title" data-diagnostic><span>${h.esc(name)}</span>${h.pill(level)}</div>`
      + `<div class="d-msg" data-diagnostic>${h.esc(check ? check.message || '—' : 'no current report: its source stopped computing it')}${check && check.code ? ` <span class="mono muted">${h.esc(check.code)}</span>` : ''}</div>`
      + `<div class="d-path">${h.esc(node.path)}</div></div>`;
    // the lane and its measurement trends on one axis
    const holdS = status ? status.stale_after_s : null;
    let trend = axisRow(axis, h, { name: '<span class="d2-key">over the window</span>', empty: hist.error ? `no history (${hist.error})` : 'loading history…' });
    trend += `<div class="d2-lane d2-roll" data-diagnostic><div class="d2-name"><span class="d2-key">level</span></div>${spanTrack(status, axis, { maxTicks: MAX_TICKS })}<div class="d2-read">${spanReadout(status, check, h)}</div></div>`;
    // every measurement key the history holds, then any the current report has that the window holds no sample of
    const seriesList = [...((status && status.measurements) || [])];
    for (const m of (check && check.measurements) || []) if (!seriesList.some((series) => series.key === m.key)) seriesList.push({ key: m.key, unit: m.unit, points: [] });
    for (const series of seriesList) {
      const current = check && check.measurements.find((m) => m.key === series.key);
      const lastPoint = series.points[series.points.length - 1];
      let read;
      if (hoverT !== null) {
        const at = seriesValueAt(series, hoverT, holdS);
        read = at.state === 'value' ? `<span class="d2-msg">${h.esc(valueText(at.point[1], series.unit))}</span><span class="d2-when">${clock(at.point[0])}</span>`
          : `<span class="d2-msg">${at.state === 'stale' ? `no sample since ${clock(at.point[0])}` : 'no data'}</span>`;
      } else if (current) {
        read = `${h.shape(current.level)}<span class="d2-msg">${h.esc(current.text)}</span>${lastPoint ? '' : '<span class="d2-when">no sample in the window</span>'}`;
      } else {
        read = lastPoint ? `<span class="d2-msg">${h.esc(valueText(lastPoint[1], series.unit))}</span>` : '<span class="d2-msg">no data</span>';
      }
      trend += `<div class="d2-lane d2-sub" data-diagnostic title="${h.esc(series.key)}"><div class="d2-name"><span class="d2-key">${h.esc(series.key)}</span></div>${sparkTrack([series], axis, holdS)}<div class="d2-read">${read}</div></div>`;
    }
    const trendNote = `<div class="d2-note">ticks under the level lane mark reports; between them the level is held until it goes stale (${status && status.stale_after_s ? `after ${h.esc(dur(status.stale_after_s))}` : 'event-driven: held until the next report'}).</div>`;
    // what to check (authored lines), the message history (level spans, newest first), values, metadata
    const values = check ? Object.fromEntries(check.values.map((v) => [v.key, v.value])) : {};
    const why = values.what_to_check ? String(values.what_to_check).split('\n').filter((line) => line.trim()).map((line) => `<div>${h.esc(line)}</div>`).join('') : '';
    const spans = status ? [...status.levels].reverse() : [];
    const history = spans.length ? `<table class="d2-hist"><tbody>${spans.map((span) => `<tr class="${span.level ? '' : 'd2-none'}"${PROBLEM.has(span.level) ? ' data-diagnostic' : ''}>`
      + `<td class="d2-t">${clock(span.from)}–${clock(span.to)}</td><td class="d2-s">${h.shape(span.level)}</td>`
      + `<td class="d2-c">${h.esc(span.level ? span.code || span.level : 'no data')}</td><td>${h.esc(span.level ? span.message || '—' : '')}</td></tr>`).join('')}</tbody></table>` : '';
    const valueRows = check ? [
      ...check.measurements.map((m) => [m.key, `${h.shape(m.level)} ${h.esc(m.text)}`]),
      ...check.values.filter((v) => v.key !== 'what_to_check').map((v) => [v.key, h.esc(v.value)]),
    ] : [];
    const meta = check ? h.kv([
      ['level', `${h.pill(check.effective_level)}${check.level !== check.effective_level ? ` <span class="muted">reported ${h.esc(check.level)}</span>` : ''}`],
      ['code', check.code ? `<span class="mono">${h.esc(check.code)}</span>` : '<span class="muted">none</span>'],
      ['source', h.esc(check.source || '—')],
      check.hardware_id ? ['hardware id', `<span class="mono">${h.esc(check.hardware_id)}</span>`] : null,
      ['expected period', check.expected_period_s ? `every ${h.esc(check.expected_period_s)} s · stale after ${h.esc(check.stale_after_s)} s` : 'event-driven (never stale)'],
      ['last received', `${h.esc((check.received_iso || '').replace('T', ' ').slice(0, 19) || '—')} · ${h.esc(h.ago(check.age_s))} ago`],
      check.caused_by && check.caused_by.length ? ['caused by', check.caused_by.map((path) => `<a class="link" data-go="${h.esc(path)}">${h.esc((ctx.byPath.get(path) || {}).name || path)}</a>`).join(' · ')] : null,
    ]) : '';
    const windowText = axis ? `last ${h.esc(dur(axis.window || axis.to - axis.from))}` : '';
    return `${head}<div class="d-body d2-root">`
      + s('d2-trend', 'Over time', `<div class="d2-tl">${trend}</div>${trendNote}`, { right: `${windowText} · hover to scrub` })
      + s('why', 'What to check', why ? `<div class="why-lines">${why}</div>` : '')
      + s('d2-messages', `Message history · ${spans.length}`, history, { right: 'newest first · a run of equal reports is one row' })
      + s('d2-values', 'Values', h.kv(valueRows))
      + s('d2-meta', 'Metadata', meta)
      + '</div>';
  }

  // ------------------------------------------------------------------------------- a grouping row ----
  function groupTimeline(ctx) {
    const h = ctx.h;
    const members = ctx.group.children.filter((child) => child.entity).slice(0, 40);
    let axis = null;
    const lanes = members.map((child) => {
      const bar = want('bar', child.entity);
      const data = bar.data && bar.data.path === child.entity ? bar.data : null;
      if (data && !axis) axis = { from: data.from, to: data.to, window: data.window_s };
      return { child, data, own: data ? data.rows.find((row) => row.depth === 0) : null };
    });
    let html = axisRow(axis, h, { name: '<span class="d2-key">member</span>' });
    for (const lane of lanes) {
      const at = hoverT !== null ? binAt(lane.data, lane.own, hoverT) : undefined;
      const read = hoverT !== null ? (at && at.level ? `${h.shape(at.level)}<span class="d2-msg">worst ${h.esc(at.level)}</span>` : '<span class="d2-msg">no data</span>')
        : `${h.statusMark(lane.child)}<span class="d2-msg" title="${h.esc(lane.child.message || '')}">${h.esc(usbText(lane.child.message || ''))}</span>`;
      html += `<div class="d2-lane" data-diagnostic data-go="${h.esc(lane.child.entity)}"><div class="d2-name"><span class="d2-key">${h.esc(lane.child.label)}</span></div>`
        + `${barTrack(lane.data, lane.own, axis)}<div class="d2-read">${read}</div></div>`;
    }
    return `<div class="d2-tl">${html}</div>`;
  }

  // ---------------------------------------------------------------------------------- the events ----
  // a lane opens its diagnostic, the crumb goes back: both through the page's drill (one history entry each)
  if (typeof document !== 'undefined') {
    document.addEventListener('click', (event) => {
      const t = event.target;
      if (!t.closest || !t.closest('#detail .d2-root, #detail .d-head') || t.closest('.ent-tools')) return;
      if (t.closest('[data-d2-back]')) { hoverT = null; if (last) last.ctx.closeDiagnostic(); return; }
      if (t.closest('[data-go]')) return; // the page selects that entity
      const lane = t.closest('[data-d2-diag]');
      if (lane && last && last.ctx.node) { hoverT = null; last.ctx.openDiagnostic(lane.dataset.d2Diag); }
    });
    document.addEventListener('keydown', (event) => {
      const lane = event.target.closest && event.target.closest('#detail .d2-root [data-d2-diag]');
      if (!lane || (event.key !== 'Enter' && event.key !== ' ')) return;
      event.preventDefault();
      if (last && last.ctx.node) { hoverT = null; last.ctx.openDiagnostic(lane.dataset.d2Diag); }
    });
    // scrub: only over the track column (the names and messages stay readable under the pointer)
    let frame = 0;
    document.addEventListener('mousemove', (event) => {
      const tl = event.target.closest && event.target.closest('#detail .d2-tl');
      let next = null;
      if (tl && last && last.axis) {
        const track = tl.querySelector('.d2-axis .d2-track');
        const rect = track && track.getBoundingClientRect();
        if (rect && rect.width > 0 && event.clientX >= rect.left && event.clientX <= rect.right) {
          next = last.axis.from + ((event.clientX - rect.left) / rect.width) * (last.axis.to - last.axis.from);
        }
      }
      if (next === hoverT) return;
      hoverT = next;
      if (!frame) frame = requestAnimationFrame(() => { frame = 0; redraw(); });
    });
    document.addEventListener('mouseout', (event) => {
      if (event.relatedTarget || hoverT === null) return; // the pointer left the window
      hoverT = null;
      redraw();
    });
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d2 = {
    name: 'State timeline',
    description: 'Grafana state-timeline / logic analyser: each diagnostic is a lane on one time axis over the app-wide window, its current level and message to the right; hover scrubs every lane, a click opens the diagnostic with its trends on the same axis. Children under a header.',
    order: 2,
    // container = #detail; detail = GET /api/v1/node for the selection (null while it loads, or for a grouping row);
    // ctx = the page's building blocks (docs/round2-seams.md §6). Called every second: ctx.draw() skips an unchanged page.
    render(container, detail, ctx) {
      injectStyle();
      const p = ctx.parts;
      const s = ctx.section;
      last = { container, detail, ctx, axis: null };
      if (ctx.group) {
        const body = groupTimeline(ctx);
        last.axis = (() => { const m = ctx.group.children.find((c) => c.entity && got('bar', c.entity).data); const d = m && got('bar', m.entity).data; return d ? { from: d.from, to: d.to } : null; })();
        ctx.draw(`${p.head()}<div class="d-body d2-root">${s('d2-members', `Members · ${ctx.counts.members}`, body, { right: 'each member\'s health bar · click one to open it' })}`
          + `${s('links', 'Link health', p.links(), { right: 'the computer diagnoses its own wires' })}</div>`);
        return;
      }
      if (!detail) {
        ctx.draw(`${p.head()}${p.banners()}<div class="d-body d2-root"><div class="muted">loading…</div></div>`);
        return;
      }
      const path = ctx.node.path;
      const histEntry = got('hist', path);
      // the page's drill: a diagnostic of this entity that it reports, or that its history still holds
      const open = ctx.diagnostic && ctx.diagnostic.path === path ? ctx.diagnostic.name : null;
      if (open) {
        const known = detail.checks.some((check) => check.name === open)
          || (histEntry.data && histEntry.data.statuses.some((status) => status.name === open));
        if (known) {
          const html = diagnosticPage(ctx, detail, open);
          last.axis = entityAxis(got('hist', path), null);
          ctx.draw(html);
          return;
        }
      }
      const timeline = entityTimeline(ctx, detail);
      last.axis = entityAxis(got('hist', path), got('bar', path));
      const windowS = last.axis ? last.axis.window || last.axis.to - last.axis.from : null;
      // WHY no banners above the lanes: the status table comes first; the knock-on and not-connected facts are in the
      // rolls-up line, and stale is each lane's own readout and hatching
      ctx.draw(`${p.head()}<div class="d-body d2-root">`
        + rollsUp(ctx, detail, ctx.h)
        + s('d2-timeline', `Diagnostics · ${detail.checks.length}`, timeline, { right: `${windowS ? `last ${ctx.h.esc(dur(windowS))} · ` : ''}hover to scrub · click a lane to open it` })
        + s('why', 'What to check', p.whatToCheck())
        + s('faults', 'Faults', p.faults())
        + s('links', 'Link health', p.links(), { right: 'the computer diagnoses its own wires' })
        + s('setup', 'Expected setup', p.setup(), { right: 'the rig file, never the device' })
        + s('identity', 'Identity', p.identity())
        + s('connection', `Connection (folded) · ${ctx.counts.connection}`, p.connection(), { right: 'the layers this row is reached through, folded into it' })
        + s('parts', `Parts · ${ctx.counts.parts}`, p.parts(), { right: 'composition (part_of), folded into this row' })
        + s('events', 'Events · this row and below', p.events())
        + '</div>');
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
