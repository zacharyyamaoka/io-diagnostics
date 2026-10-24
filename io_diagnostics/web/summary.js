// The header counts, one meaning per mark (Zach, 2026-10-24: "I am a bit confused right now with the icons and colors …
// I feel that here it should always be listed in order of increasing severity").
//
//   window.IOSummary = { LEVELS, WORDS, shape(level, label), html(summary, {filter}), render(el, summary, {filter, onFilter}) }
//
// The marks are the diagnostics levels only, ROS DiagnosticStatus plus stale (contract/record.py LEVELS + STALE):
//   ◷ stale    known, but not heard lately (the hollow dot on a row)
//   ? unknown  plugged in, but RobotDx could not check it (for example tview has the port)
//   ▲ warn     something is off
//   ✖ error    broken
// Left to right in that order, increasing severity; a zero is hidden; the shapes and colours are the rows' own (the
// same SHAPES the tree draws). Hovering the counts explains each mark; clicking a count filters the tree to the rows
// with that level, clicking it again clears the filter.
//
// Loads in the page (window.IOSummary) and in Node (module.exports) so tests/test_view_core.js can read the order.
'use strict';
(function (root) {
  const LEVELS = ['stale', 'unknown', 'warn', 'error']; // increasing severity, left to right
  const WORDS = { stale: 'stale', unknown: 'unknown', warn: 'warning', error: 'error' };
  // Plain words for each mark (the hover legend and the marks' tooltips)
  const MEANING = {
    stale: 'Known, but not heard from lately. Drawn as a hollow dot on its row.',
    unknown: "Plugged in, but RobotDx couldn't check it (for example tview has the port).",
    warn: 'Something is off, and it still works.',
    error: 'Broken.',
  };
  // 16-unit status SHAPES: the shape alone must read without colour; a glyph is knocked out in --io-on-status.
  const SHAPES = {
    warn: '<path class="t6-fill" d="M7.13 2.2a1 1 0 0 1 1.74 0l6.03 10.6a1 1 0 0 1-.87 1.5H1.97a1 1 0 0 1-.87-1.5Z"/>'
      + '<path class="t6-ink" d="M8 6.1v3.4"/><circle class="t6-ink-dot" cx="8" cy="11.7" r=".95"/>',
    error: '<path class="t6-fill" d="M5.3 1.2h5.4l4.1 4.1v5.4l-4.1 4.1H5.3l-4.1-4.1V5.3Z"/>'
      + '<path class="t6-ink" d="m5.7 5.7 4.6 4.6m0-4.6-4.6 4.6"/>',
    unknown: '<circle class="t6-fill" cx="8" cy="8" r="6.8"/>'
      + '<path class="t6-ink" d="M6.25 6.3a1.85 1.85 0 0 1 3.6.6c0 1.25-1.85 1.5-1.85 2.75"/><circle class="t6-ink-dot" cx="8" cy="11.6" r=".9"/>',
    // the same hollow ring as the row's presence dot, so the header and the rows say stale the same way
    stale: '<circle class="t6-line" cx="8" cy="8" r="4.6"/>',
  };

  const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const shape = (level, label) => `<svg class="t6-shape t6-${level}" viewBox="0 0 16 16" role="img" aria-label="${esc(label || WORDS[level])}">${SHAPES[level]}</svg>`;

  function html(summary, opts = {}) {
    const counts = (summary && summary.counts) || {};
    const shown = LEVELS.filter((level) => counts[level]);
    if (!shown.length) return '<span class="t6-quiet">nothing needs you</span>';
    const parts = shown.map((level) => {
      const n = counts[level];
      const on = opts.filter === level;
      return `<button type="button" class="t6-count${on ? ' t6-on' : ''}" data-t6-count="${level}" aria-pressed="${on}"`
        + ` title="${n} independent ${WORDS[level]}${n > 1 ? 's' : ''} · click to ${on ? 'show everything' : `show only the ${WORDS[level]} rows`}">`
        + `${shape(level, WORDS[level])}<span class="t6-n">${n}</span></button>`;
    });
    const legend = LEVELS.map((level) => `<div class="t6-leg-row">${shape(level, WORDS[level])}<span><b>${WORDS[level]}</b> · ${esc(MEANING[level])}</span></div>`).join('');
    return `<span class="t6-sum">${parts.join('')}<span class="t6-legend" role="tooltip">${legend}</span></span>`;
  }

  // Idempotent: the page calls it on every tick; the DOM is only touched when the markup changed.
  function render(container, summary, opts = {}) {
    if (!container) return;
    const markup = html(summary, opts);
    if (container.__ioSummaryHtml !== markup) { container.innerHTML = markup; container.__ioSummaryHtml = markup; }
    if (!container.__ioSummaryBound) {
      container.__ioSummaryBound = true;
      container.addEventListener('click', (event) => {
        const count = event.target.closest('[data-t6-count]');
        if (!count || typeof container.__ioSummaryFilter !== 'function') return;
        const level = count.getAttribute('data-t6-count');
        container.__ioSummaryFilter(container.__ioSummaryActive === level ? null : level);
      });
    }
    container.__ioSummaryFilter = opts.onFilter;
    container.__ioSummaryActive = opts.filter || null;
  }

  const IOSummary = { LEVELS, WORDS, MEANING, SHAPES, shape, html, render };
  root.IOSummary = IOSummary;
  if (typeof module !== 'undefined' && module.exports) module.exports = IOSummary;
})(typeof window !== 'undefined' ? window : globalThis);
