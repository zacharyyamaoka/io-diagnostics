// Detail layout d1c · State timeline. Grafana's state timeline for the Diagnostics section (the chart Zach pointed at,
// 2026-10-24: "I do like these uptime charts ... you kinda like discretize them right"): one lane per diagnostic across the
// WHOLE history window, name on the left, current status on the right, the selected lane's message and what-to-check
// right under it. The best of the five for "when did it go bad". The entity's own health bar sits on top as the overall
// lane. Health only. Base: detail/d1x.js; chart: detail/health.js.
'use strict';
(function (root) {
  const X = () => root.IODiagX;
  const CSS = `
.dc-top { margin-bottom: 8px; }
.dc-x { margin: 2px 0 8px; padding: 6px 10px 8px; border-left: 2px solid var(--io-selected-line); background: var(--io-surface); border-radius: 0 var(--io-radius-sm) var(--io-radius-sm) 0; }
.dc-ax { display: grid; grid-template-columns: var(--dx-name) minmax(0, 1fr) var(--dx-status); gap: 10px; }
.dc-ax > div { grid-column: 2; }
`;

  function section(node, detail, ctx) {
    const x = X();
    const H = root.IOHealth;
    return x.section(node, detail, ctx, (m, hist) => {
      const worst = m.rows.length && m.rows[0].bad ? m.rows[0].name : null;
      const path = node.path;
      const selected = x.ui.sel.has(path) ? x.ui.sel.get(path) : worst;
      const axis = hist.data ? `<div class="dc-ax" style="--dx-name:${Math.min(220, Math.round((x.last() ? x.last().width : 600) * 0.3))}px;--dx-status:76px"><div>${H.axis({ window_s: hist.data.to - hist.data.from, bin_s: 0, bins: 1 }, `${H.clock(hist.data.from)} – ${H.clock(hist.data.to)} · worst per cell`)}</div></div>` : '';
      return `<div class="dc-top">${x.summary(node, m, ctx, hist, { expandable: false, noAxis: true })}</div>${x.toolbar(node)}${axis}`
        + x.lanes(node, m, ctx, hist, { select: true, selected, after: (row) => (row.name === selected ? `<div class="dc-x" data-diagnostic>${x.body(row, ctx)}</div>` : '') })
        + H.legend();
    }, (m) => (m.problems.length ? `${x.plural(m.problems.length, 'problem')} first · click a lane for its message` : 'click a lane for its message'));
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d1c = {
    name: 'State timeline lanes',
    description: 'Grafana-style: one lane per diagnostic across the whole history window, name on the left, current status on the right, the selected lane\'s message and what-to-check under it. The best view for "when did it go bad".',
    order: 1.3,
    diagnostics: (node, detail, ctx) => section(node, detail, ctx),
    render(container, detail, ctx) { X().page(container, detail, ctx, 'd1c', section, CSS); },
  };
})(typeof window !== 'undefined' ? window : globalThis);
