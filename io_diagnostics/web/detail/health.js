// The health-history chart: ONE renderer for every place the page draws a health history (the entity's health bar, a
// diagnostic's own timeline, a child's mini bar, a row-end spark preview, a timeline lane). Loaded from detail/index.json
// like a layout (it registers no render(), so the Prototype picker never lists it); layouts call it at draw time.
// WHY a discrete state timeline: Zach, 2026-10-24: "I do like these uptime charts ... you kinda like discretize them
// right", "the aspect ratio of these figures seems a bit off ... make it more like the rectangles so at a glance I
// instantly know what type of chart it is", "for some of these bars they almost look like a solid line because not
// clear spacing ... where these spark previews are much better". So: separate rectangles, a visible gap, taller than
// wide (Grafana's state-timeline proportions), one per time bin; never a line, never confusable with a value sparkline.
// WHY Statuspage's colours and proportions: Zach, 2026-10-24, shown Atlassian Statuspage's "uptime over the past 90 days" strip:
// "my eye is trained actually to look for green as OK. when I see OK and grey I think it's something else", and "I like the aspect
// ratio more than before but I still think perhaps too wide ... thinner is better because you get more resolution". So no new
// convention: the palette is Statuspage's own (PALETTE below, with its source) and the bars are Statuspage's shape: thin, fixed,
// tall, with a gap.
// THE RULE for how many bars: each bar is BAR_W (3 px) wide with a GAP (2 px) between, so the count is what the width holds,
// floor((width + gap) / (3 + 2)), and each bar covers History window / count seconds. A wider pane gives more bars (more
// resolution), the History window sets the span, and the gap never shrinks, so the bars never run into a line.
// Colours: OK a muted Statuspage green, WARN its yellow, ERROR its red, no data empty (an outline, no fill), stale a mid grey,
// unknown a darker one; nothing is hatched. Each cell's hover (data-tip, drawn as a small tooltip: a title would not show in a
// screenshot or a recording) gives its time range and the worst level any status held in it.
'use strict';
(function (root) {
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };   // the API's rank order: ok < warn < unknown < stale < error
  const WORDS = { ok: 'OK', warn: 'WARN', error: 'ERROR', stale: 'STALE', unknown: 'UNKNOWN' };
  // The bar: BAR_W wide, GAP apart (Statuspage's proportions), as tall as its kind says.
  const BAR_W = 3;
  const GAP = 2;
  const KINDS = {
    timeline: { gap: GAP, h: 24 },   // the entity's health bar, a diagnostic's own timeline (Statuspage's bars are 25 px)
    lane: { gap: GAP, h: 20 },       // one lane of a state timeline
    spark: { gap: GAP, h: 14 },      // the small preview at the end of a row
  };

  const pad = (n) => String(n).padStart(2, '0');
  const clock = (t) => { const d = new Date(t * 1000); return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
  const span = (s) => (s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : `${+(s / 3600).toFixed(1)} h`);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const rankOf = (level) => (level in RANK ? RANK[level] : -1);

  // How many bars fit: as many 3 px bars with 2 px gaps as the width holds. The window does not change the count, it changes what
  // each bar covers (window / count); `windowS` is only here so a caller says which window it draws.
  function count(windowS, widthPx, kind = 'timeline') {
    const k = KINDS[kind] || KINDS.timeline;
    const width = Math.max(widthPx || 0, 40);
    return Math.max(4, Math.floor((width + k.gap) / (BAR_W + k.gap)));
  }

  // The width a layout has to draw in: the detail's reading column (the page's --io-detail-max) inside its padding.
  function widthOf(container, reserve = 0) {
    if (!container || typeof document === 'undefined') return 600 - reserve;
    const max = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--io-detail-max')) || 760;
    return Math.max(80, Math.min(container.clientWidth - 36, max) - reserve);
  }

  // A cell is {level|null, from, to, message?}. The worst level any span held in the bin (the API's rule); null = nothing held.
  function fromLevels(levels, from, to, n) {
    const binS = (to - from) / n;
    const cells = [];
    for (let at = 0; at < n; at += 1) {
      const t0 = from + at * binS;
      const t1 = t0 + binS;
      let worst = null;
      let message = '';
      for (const item of levels || []) {
        if (item.level === null || item.level === undefined || item.to <= t0 || item.from >= t1) continue;
        if (worst === null || rankOf(item.level) > rankOf(worst)) { worst = item.level; message = item.message || ''; }
      }
      cells.push({ level: worst, from: t0, to: t1, message });
    }
    return cells;
  }
  function fromBar(bins, from, binS) {
    return (bins || []).map((level, at) => ({ level: level || null, from: from + at * binS, to: from + (at + 1) * binS, message: '' }));
  }

  // The cells of a bar whose bins another layout already holds, re-binned to the count that fits (worst of the bins merged):
  // the older layouts (d2-d5) ask the bar API for their own bin count; this draws theirs as the same discrete chart.
  function rebin(cells, n) {
    if (!cells || !cells.length || cells.length <= n) return cells || [];
    const out = [];
    for (let at = 0; at < n; at += 1) {
      const first = Math.floor((at * cells.length) / n);
      const end = Math.max(first + 1, Math.floor(((at + 1) * cells.length) / n));
      const group = cells.slice(first, end);
      let worst = null;
      let message = '';
      for (const cell of group) if (cell.level && (worst === null || rankOf(cell.level) > rankOf(worst))) { worst = cell.level; message = cell.message || ''; }
      out.push({ level: worst, from: group[0].from, to: group[group.length - 1].to, message });
    }
    return out;
  }
  const fromBins = (bins) => (bins || []).map((level) => ({ level: level || null, from: null, to: null, message: '' }));

  const tip = (cell) => `${cell.from === null || cell.from === undefined ? '' : `${clock(cell.from)}–${clock(cell.to)} · `}${cell.level ? `worst: ${cell.level}` : 'no data'}${cell.message ? ` · ${cell.message}` : ''}`;
  // The chart: a grid of separate cells. data-diagnostic: its warn / error colours are a diagnostic result (the warn-colours rule).
  function html(cells, opts = {}) {
    if (!cells || !cells.length) return '';
    const k = KINDS[opts.kind] || KINDS.timeline;
    const label = opts.label || 'health over the window';
    return `<div class="ioh ioh-${opts.kind || 'timeline'}${opts.cls ? ` ${esc(opts.cls)}` : ''}" data-diagnostic data-ioh role="img" aria-label="${esc(label)}"`
      + ` style="--ioh-n:${cells.length};--ioh-h:${k.h}px;--ioh-gap:${k.gap}px">${cells.map((cell) => `<i class="${cell.level ? esc(cell.level) : 'none'}" data-tip="${esc(tip(cell))}"></i>`).join('')}</div>`;
  }
  const axis = (data, text, cls) => `<div class="ioh-axis${cls ? ` ${esc(cls)}` : ''}"><span>−${span(data.window_s)}</span><span>${esc(text || `worst per ${span(data.bin_s || data.window_s / (data.bins || 60))}`)}</span><span>now</span></div>`;
  const legend = () => '<div class="ioh-legend" aria-label="how to read the cells">'
    + '<span><i class="ok"></i>OK</span><span><i class="warn"></i>warn</span><span><i class="error"></i>error</span>'
    + '<span><i class="stale"></i>stale</span><span><i class="unknown"></i>unknown</span><span><i class="none"></i>no data</span></div>';

  // ------------------------------------------------------------------------------------------ colours ----
  // ONE palette token set for every health cell on the page (the d1 variants, the row-end sparks, the status-line lanes, the entity
  // page's Diagnostics tab, d2-d5), a light and a dark value. It is Atlassian Statuspage's own, not invented:
  //   source: the stylesheet every Statuspage status page loads (status_manifest-*.css on dka575ofm4ao0.cloudfront.net, read from
  //   githubstatus.com on 2026-10-24), the uptime strip's unit-container rules:
  //     .unit-container.green  { background-color: #2ecc71 }   (also .color-green, .status-none)
  //     .unit-container.yellow { background-color: #f1c40f }
  //     .unit-container.red    { background-color: #e74c3c }
  //   and the strip's shape: .unit-container { height: 25px; margin-left: .75% } (thin bars, a gap).
  // WARN is that yellow and ERROR that red, unchanged, in both themes. OK is that green "slightly muted so it's quiet": 70 % of
  // #2ecc71 mixed with 30 % of the neutral grey #9a9a9a is #4ebd7d. No data is EMPTY (no fill, a faint outline), as Zach asked, where
  // Statuspage draws a light grey bar. Stale and unknown are two greys, never hatched; the heavier one is UNKNOWN (can't tell:
  // look), the lighter STALE (it had data and lost it). In the light theme heavier is darker; in the dark theme a grey darker than
  // the page vanishes, so heavier is lighter there: the order of weight is kept, the direction of lightness flips.
  // The numbers (contrast against the page, how far apart the greys are): docs/health_palettes.py reads this table.
  const PALETTE = {
    light: { ok: '#4ebd7d', warn: '#f1c40f', error: '#e74c3c', stale: '#8f8f98', unknown: '#4b4b53', ink: '#1d1d20' },
    dark: { ok: '#4ebd7d', warn: '#f1c40f', error: '#e74c3c', stale: '#72727c', unknown: '#c8c8d0', ink: '#1d1d20' },
  };
  function paletteCss() {
    const tokens = (p) => `--hc-ok: ${p.ok}; --hc-warn: ${p.warn}; --hc-error: ${p.error}; --hc-stale: ${p.stale}; --hc-unknown: ${p.unknown}; --hc-ink: ${p.ink};`;   // ink: the mark inside a warn shape (the yellow is light, so its ink is dark in both themes)
    return `:root { ${tokens(PALETTE.light)} }\n:root[data-mode="dark"] { ${tokens(PALETTE.dark)} }`;
  }

  const CSS = `
${paletteCss()}
.ioh { --hc-none: color-mix(in srgb, var(--io-text) 28%, transparent);
  display: grid; grid-auto-flow: column; grid-auto-columns: minmax(0, 1fr); gap: var(--ioh-gap, 2px); height: var(--ioh-h, 22px); min-width: 0; }
.ioh > i, .ioh-legend i { display: block; min-width: 0; border-radius: 1px; background: var(--hc-ok); }
.ioh > i:hover { outline: 1px solid var(--io-text-strong); outline-offset: 1px; }
.ioh i.warn { background: var(--hc-warn); }
.ioh i.error { background: var(--hc-error); }
.ioh i.unknown { background: var(--hc-unknown); }
.ioh i.stale { background: var(--hc-stale); }
.ioh i.none { background: transparent; box-shadow: inset 0 0 0 1px var(--hc-none); }
.ioh-axis { display: flex; justify-content: space-between; gap: 10px; margin-top: 3px; font-size: 11px; color: var(--io-faint); font-variant-numeric: tabular-nums; }
.ioh-axis span:nth-child(2) { text-align: center; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ioh-legend { --hc-none: color-mix(in srgb, var(--io-text) 28%, transparent); display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 6px; font-size: 11px; color: var(--io-faint); }
.ioh-legend span { display: inline-flex; align-items: center; gap: 5px; }
.ioh-legend i { width: 7px; height: 12px; }
.ioh-legend i.warn { background: var(--hc-warn); }
.ioh-legend i.error { background: var(--hc-error); }
.ioh-legend i.unknown { background: var(--hc-unknown); }
.ioh-legend i.stale { background: var(--hc-stale); }
.ioh-legend i.none { background: transparent; box-shadow: inset 0 0 0 1px var(--hc-none); }
.ioh-none { color: var(--io-faint); font-size: 11px; white-space: nowrap; }
.ioh-tip { display: none; position: fixed; z-index: 1000; pointer-events: none; padding: 3px 8px; border-radius: var(--io-radius-sm); background: var(--io-popover); color: var(--io-text-strong); box-shadow: 0 0 0 1px var(--io-border), var(--io-shadow-pop); font: 11.5px var(--io-font-ui); font-variant-numeric: tabular-nums; white-space: nowrap; }
`;
  // One tooltip for every cell, drawn above the hovered cell (clamped to the window): "08:55:30–08:55:45 · worst: warn".
  let tipEl = null;
  function showTip(cell) {
    if (!tipEl) { tipEl = document.createElement('div'); tipEl.className = 'ioh-tip'; tipEl.setAttribute('role', 'tooltip'); document.body.appendChild(tipEl); }
    tipEl.textContent = cell.dataset.tip;
    tipEl.style.display = 'block';
    const at = cell.getBoundingClientRect();
    const box = tipEl.getBoundingClientRect();
    const left = Math.min(Math.max(4, at.left + at.width / 2 - box.width / 2), window.innerWidth - box.width - 4);
    const top = at.top - box.height - 6 < 4 ? at.bottom + 6 : at.top - box.height - 6;
    tipEl.style.left = `${left}px`;
    tipEl.style.top = `${top}px`;
  }
  const hideTip = () => { if (tipEl) tipEl.style.display = 'none'; };

  function install() {
    if (typeof document === 'undefined' || document.querySelector('style[data-layout="health"]')) return;
    const style = document.createElement('style');
    style.dataset.layout = 'health';
    style.textContent = CSS;
    document.head.appendChild(style);
    document.addEventListener('mouseover', (event) => {
      const cell = event.target instanceof Element ? event.target.closest('.ioh > i[data-tip]') : null;
      if (cell) showTip(cell); else hideTip();
    });
    document.addEventListener('scroll', hideTip, true);
  }
  install();

  root.IOHealth = { RANK, WORDS, KINDS, BAR_W, GAP, PALETTE, count, widthOf, fromLevels, fromBar, fromBins, rebin, html, axis, legend, clock, span, esc, rankOf, install };
})(typeof window !== 'undefined' ? window : globalThis);
