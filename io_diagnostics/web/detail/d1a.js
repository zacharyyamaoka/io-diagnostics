// Detail layout d1a · Single-line table: Zach's own spec for the Diagnostics section, 2026-10-24: "For diagnostic its like
// name, the message, current status (OK, WARNING, ETC) and then optionally the sparkline for the health", one line per row.
// d1's table with its columns reordered to Name | Message | Current | History, every row ONE line (the message cut with
// an ellipsis, the full text on hover and one click away), sorted by severity then name (or A-Z, or by hand), dot-notation names
// folded into folders, ★ pins on top (detail/d6-rows.js, the entity page's row model). The status line at the top (the
// entity's health bar) drops down into one timeline lane per diagnostic. Health only: no value, no value sparkline.
// Shared base: detail/d1x.js; chart: detail/health.js.
'use strict';
(function (root) {
  const X = () => root.IODiagX;

  function section(node, detail, ctx) {
    const x = X();
    const W = x.last() ? x.last().width : 600;
    return x.section(node, detail, ctx, (m, hist) => x.summary(node, m, ctx, hist) + x.toolbar(node)
      + `<div class="dx-tab" style="${x.lineStyle(W)}"><div class="dx-hd"><span>Name</span><span>Message</span><span>Current</span><span>History</span></div>`
      + `${x.walk(node, x.planOf(node, m.rows), (r) => x.line(r.row, ctx, hist, { r }))}</div>`,
    (m) => (m.problems.length ? `${x.plural(m.problems.length, 'problem')} first · click a row for what to check` : 'click a row for the full message'));
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d1a = {
    name: 'Single-line table',
    description: 'd1\'s status monitor in Zach\'s own column order: Name | Message | Current | History, one line per row, worst first. The status line drops down into one timeline lane per diagnostic. Health only.',
    order: 1.1,
    diagnostics: (node, detail, ctx) => section(node, detail, ctx),
    // the section inside another page (detail/d6.js, the entity page's Diagnostics tab): X().embed sets its CSS and clicks up
    embed: (container, node, detail, ctx, hooks) => X().embed(container, detail, ctx, 'd1a', section, null, hooks),
    render(container, detail, ctx) { X().page(container, detail, ctx, 'd1a', section); },
  };
})(typeof window !== 'undefined' ? window : globalThis);
