// Detail layout d1b · Problems first. d1's "problems first, OK quiet" taken literally: every WARN / ERROR / STALE / UNKNOWN
// diagnostic is a short list entry that carries its own numbered what-to-check, then ALL the OK diagnostics collapse into
// one "N OK" line (with their own health timeline) that opens into the single-line rows. A healthy entity is one calm line.
// The status line at the top drops down into one timeline lane per diagnostic. Health only. Base: detail/d1x.js.
'use strict';
(function (root) {
  const usbText = (text) => (root.IOUsb ? root.IOUsb.label(text) : String(text ?? '')); // a USB link is named by web/usb.js, never "480 Mb/s"
  const X = () => root.IODiagX;
  const CSS = `
.db-list { display: grid; gap: 8px; margin: 6px 0 10px; }
.db-p { border: 1px solid var(--io-border-soft); border-radius: var(--io-radius); padding: 8px 10px; display: grid; gap: 6px; min-width: 0; background: var(--io-surface); }
.db-top { display: grid; grid-template-columns: auto minmax(0, 1fr) 76px var(--dx-spark); gap: 10px; align-items: center; }
.db-top .shape { width: 14px; height: 14px; }
.db-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; color: var(--io-text-strong); }
.db-msg { overflow-wrap: anywhere; color: var(--io-text); font-size: 12.5px; }
.db-ok { margin-top: 4px; }
.db-okline { display: grid; grid-template-columns: 14px auto minmax(0, 1fr); gap: 10px; align-items: center; width: 100%; padding: 6px 4px; border: 0; border-top: 1px solid var(--io-border-soft); background: none; color: var(--io-muted); font: inherit; font-size: 12.5px; text-align: left; cursor: pointer; border-radius: var(--io-radius-sm); }
.db-okline:hover { background: var(--io-hover); }
.db-okline:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.db-calm { padding: 6px 2px; color: var(--io-muted); font-size: 12.5px; }
`;

  function section(node, detail, ctx) {
    const x = X();
    const H = root.IOHealth;
    const W = x.last() ? x.last().width : 600;
    return x.section(node, detail, ctx, (m, hist) => {
      // ★ pinned rows (any level) sit above everything as single-line rows; the problem cards and the OK folders are the rest
      const all = x.planOf(node, m.rows);
      const pinnedIds = new Set(all.pinned.map((row) => row.id));
      const pinnedHtml = all.pinned.length ? `<div class="dx-tab" style="${x.lineStyle(W)}">${x.walk(node, x.pinnedOnly(all), (r) => x.line(r.row, ctx, hist, { r }))}</div>` : '';
      const okPlan = x.planOf(node, m.ok.filter((row) => !pinnedIds.has(row.id)));
      const shownProblems = m.problems.filter((row) => !pinnedIds.has(row.id));
      const problems = shownProblems.map((row) => `<div class="db-p" data-diagnostic>`
        + `<div class="db-top">${ctx.h.shape(row.level, row.level)}<span class="db-name" title="${x.esc(row.name)}">${root.IOD1.nameHtml(row.name, ctx.h)}</span>`
        + `<span>${x.pill(ctx, row.level)}</span>${x.cellsOf(row, hist, 96, 'spark', ctx)}</div>`
        + `<div class="db-msg">${x.esc(usbText(row.message || '—'))}${row.code ? ` <span class="mono dx-code">${x.esc(row.code)}</span>` : ''}</div>`
        + (row.todo.length ? `<div class="dx-todo-h">What to check</div><ol class="dx-todo">${row.todo.map((line) => `<li>${x.esc(line)}</li>`).join('')}</ol>` : '')
        + (row.evidence ? `<div class="dx-ev">kernel evidence: ${x.esc(row.evidence)}</div>` : '')
        + `<div class="dx-more"><a class="link" data-dx-open="${x.esc(row.name)}" tabindex="0">Open this diagnostic →</a></div></div>`).join('');
      // the OK rows, one health timeline for all of them (the worst per cell over every one of their histories)
      let okTimeline = '';
      if (hist.data && m.ok.length) {
        const spans = m.ok.filter((row) => !pinnedIds.has(row.id)).flatMap((row) => (hist.byName.get(row.name) || { levels: [] }).levels);
        okTimeline = H.html(H.fromLevels(spans, hist.data.from, hist.data.to, H.count(hist.data.to - hist.data.from, W - 14 - 20 - 120, 'lane')), { kind: 'lane', label: 'the OK diagnostics: health over the window' });
      }
      const openOk = x.isOpen('ok', !m.problems.length);
      const okLine = m.ok.length - m.ok.filter((row) => pinnedIds.has(row.id)).length > 0
        ? `<div class="db-ok"><button class="db-okline" data-dx-toggle="ok"${m.problems.length ? '' : ' data-dx-default-open'} aria-expanded="${openOk}">`
          + `<svg class="ic dx-chev${openOk ? ' open' : ''}"><use href="#i-chev"/></svg><span>OK</span><span class="dx-bar">${okTimeline}</span></button>`
          + (openOk ? `<div class="dx-tab" style="${x.lineStyle(W)}">${x.toolbar(node)}${x.walk(node, okPlan, (r) => x.line(r.row, ctx, hist, { r }))}</div>` : '') + '</div>' : '';
      return x.summary(node, m, ctx, hist) + pinnedHtml
        + (shownProblems.length ? `<div class="db-list" style="--dx-spark:96px">${problems}</div>` : `<div class="db-calm">Nothing needs attention.</div>`)
        + okLine;
    }, (m) => (m.problems.length ? `${x.plural(m.problems.length, 'problem')} first, then ${m.ok.length} OK folded` : 'everything is OK'));
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d1b = {
    name: 'Problems first',
    description: 'Every problem is a short list entry with its own what-to-check; every OK diagnostic folds into one "N OK" line with a health timeline that opens into single-line rows. A healthy entity is one calm line.',
    order: 1.2,
    diagnostics: (node, detail, ctx) => section(node, detail, ctx),
    render(container, detail, ctx) { X().page(container, detail, ctx, 'd1b', section, CSS); },
  };
})(typeof window !== 'undefined' ? window : globalThis);
