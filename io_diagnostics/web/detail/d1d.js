// Detail layout d1d · rqt_robot_monitor. ROS's own diagnostics viewer, for the Diagnostics section: the statuses grouped
// Errors / Warnings / All, nested by the name's prefix (the "<who>:" before the colon, which is how ROS diagnostic_updater
// names a status), and a key / value panel for the selected one (name, message, hardware id, level, timing ...). Zach:
// "in the spirit of not reinventing the wheel" (the same day). Health only: the key / value panel holds the status's
// health facts, never its measurements. The status line at the top drops down into one timeline lane per diagnostic.
// Base: detail/d1x.js; chart: detail/health.js.
'use strict';
(function (root) {
  const X = () => root.IODiagX;
  const CSS = `
.dd-tree { margin: 6px 0 8px; font-size: 12.5px; }
.dd-g { border-bottom: 1px solid var(--io-border-soft); }
.dd-gh { display: grid; grid-template-columns: 14px minmax(0, 1fr) auto; gap: 8px; align-items: center; width: 100%; padding: 6px 2px; border: 0; background: none; font: inherit; font-weight: 600; text-align: left; cursor: pointer; border-radius: var(--io-radius-sm); color: var(--io-text-strong); }
.dd-gh:hover { background: var(--io-hover); }
.dd-gh:focus-visible, .dd-leaf:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.dd-gh .shape, .dd-leaf .shape { width: 13px; height: 13px; }
.dd-n { font-weight: 400; color: var(--io-faint); font-size: 12px; }
.dd-leaf { display: grid; grid-template-columns: 16px minmax(0, 30%) minmax(0, 1fr) var(--dx-spark); gap: 8px; align-items: center; min-height: 26px; padding: 2px 4px 2px calc(18px + var(--depth, 0) * 14px); border-radius: var(--io-radius-sm); cursor: pointer; }
.dd-leaf:hover { background: var(--io-hover); }
.dd-leaf.sel { background: var(--io-selected); box-shadow: inset 2px 0 0 var(--io-selected-line); }
.dd-leaf .dd-m { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dd-t { display: flex; align-items: center; gap: 2px; min-width: 0; }
.dd-leaf .dd-m { color: var(--io-muted); }
.dd-leaf.bad .dd-t { font-weight: 600; color: var(--io-text-strong); }
.dd-ok { width: 7px; height: 7px; border-radius: 50%; background: var(--io-border); justify-self: center; }
.dd-detail { border: 1px solid var(--io-border-soft); border-radius: var(--io-radius); padding: 10px 12px; display: grid; gap: 8px; background: var(--io-surface); min-width: 0; }
.dd-title { display: flex; align-items: center; gap: 8px; font-weight: 600; color: var(--io-text-strong); min-width: 0; }
.dd-title span:first-child { min-width: 0; overflow-wrap: anywhere; }
.dd-detail .kv { grid-template-columns: 120px 1fr; }
`;

  function detailPanel(row, ctx, hist, W) {
    const x = X();
    const h = ctx.h;
    const H = root.IOHealth;
    const check = row.check;
    const caused = (check.caused_by || []).map((path) => { const n = ctx.byPath.get(path); return `<a class="link" data-go="${x.esc(path)}">${x.esc(n ? n.name : path)}</a>`; }).join(', ');
    const status = hist.byName.get(row.name);
    const timeline = hist.data && status
      ? H.html(H.fromLevels(status.levels, hist.data.from, hist.data.to, H.count(hist.data.to - hist.data.from, W - 24, 'timeline')), { kind: 'timeline', label: `${row.name}: health over the window` })
        + H.axis({ window_s: hist.data.to - hist.data.from }, 'worst level per cell · hollow = no data') : '';
    const kv = h.kv([
      ['Message', `<span class="d1-v">${x.esc(row.message || '—')}</span>`],
      row.code ? ['Code', `<span class="mono">${x.esc(row.code)}</span>`] : null,
      ['Level', `${h.pill(row.level)}${check.level !== check.effective_level ? ` <span class="muted">reported ${x.esc(check.level)}</span>` : ''}`],
      check.hardware_id ? ['Hardware ID', `<span class="mono">${x.esc(check.hardware_id)}</span>`] : null,
      ['Source', x.esc(check.source || '—')],
      ['Expected period', check.expected_period_s ? `every ${x.num(check.expected_period_s)} s · STALE after ${x.num(check.stale_after_s)} s` : 'event-driven (never stale)'],
      ['Last received', `${x.esc((check.received_iso || '').replace('T', ' ').slice(0, 19) || '—')} · ${h.ago(check.age_s)} ago`],
      caused ? ['Caused by', caused] : null,
    ]);
    return `<div class="dd-detail"${row.bad ? ' data-diagnostic' : ''}><div class="dd-title"><span>${x.esc(row.name)}</span>${h.pill(row.level)}</div>`
      + timeline + kv
      + (row.bad && row.todo.length ? `<div class="dx-todo-h">What to check</div><ol class="dx-todo">${row.todo.map((line) => `<li>${x.esc(line)}</li>`).join('')}</ol>` : '')
      + `<div class="dx-more"><a class="link" data-dx-open="${x.esc(row.name)}" tabindex="0">Open this diagnostic →</a></div></div>`;
  }

  function section(node, detail, ctx) {
    const x = X();
    const W = x.last() ? x.last().width : 600;
    return x.section(node, detail, ctx, (m, hist) => {
      const path = node.path;
      const selected = x.ui.sel.has(path) ? x.ui.sel.get(path) : (m.rows[0] ? m.rows[0].name : '');
      const groups = [
        ['errors', 'Errors', m.rows.filter((row) => row.level === 'error'), true],
        ['warnings', 'Warnings', m.rows.filter((row) => row.bad && row.level !== 'error'), true],
        ['all', 'All', m.rows, !m.problems.length],
      ];
      // the folders are the shared row model's (dot-notation / "who: what" names, detail/d6-rows.js): ROS's prefix nesting, any depth
      const everything = x.planOf(node, m.rows);
      const pinnedIds = new Set(everything.pinned.map((row) => row.id));
      const leaf = (r) => `<div class="dd-leaf${r.row.bad ? ' bad' : ''}${r.row.name === selected ? ' sel' : ''}" style="--depth:${r.depth}" data-dx-select="${x.esc(r.row.name)}"${r.drag} role="button" tabindex="0" title="${x.esc(r.row.name)} — ${x.esc(r.row.message)}"${r.row.bad ? ' data-diagnostic' : ''}>`
        + `${r.row.bad ? ctx.h.shape(r.row.level, r.row.level) : '<span class="dd-ok" aria-label="OK"></span>'}<span class="dd-t">${r.grip}${r.star}<span class="dx-nt">${x.esc(r.flat === false ? r.leaf : r.row.name)}</span></span>`
        + `<span class="dd-m">${x.esc(r.row.message || '—')}</span><span>${x.cellsOf(r.row, hist, 72, 'spark', ctx)}</span></div>`;
      const pinnedHtml = everything.pinned.length ? `<div class="dd-g">${x.walk(node, x.pinnedOnly(everything), leaf)}</div>` : '';
      const tree = pinnedHtml + groups.map(([gid, title, allRows, defaultOpen]) => {
        const open = x.isOpen(`grp:${gid}`, defaultOpen);
        const rows = allRows.filter((row) => !pinnedIds.has(row.id));
        const worst = rows.length ? rows[0] : null;
        const body = rows.length ? x.walk(node, x.planOf(node, rows, { idPrefix: `${gid}:` }), leaf) : '';
        return `<div class="dd-g"><button class="dd-gh" data-dx-toggle="grp:${gid}"${defaultOpen ? ' data-dx-default-open' : ''} aria-expanded="${open}"><svg class="ic dx-chev${open ? ' open' : ''}"><use href="#i-chev"/></svg>`
          + `<span>${title}</span><span class="dd-n">${worst && worst.bad ? ctx.h.shape(worst.level, worst.level) : ''}</span></button>`
          + `${open ? (body || '<div class="muted dd-empty" style="padding:2px 18px 6px">none</div>') : ''}</div>`;
      }).join('');
      const picked = m.rows.find((row) => row.name === selected);
      return `${x.summary(node, m, ctx, hist)}${x.toolbar(node)}<div class="dd-tree" style="--dx-spark:72px">${tree}</div>${picked ? detailPanel(picked, ctx, hist, W) : ''}`;
    }, (m) => (m.problems.length ? `${m.rows.filter((row) => row.level === 'error').length} errors · ${m.rows.filter((row) => row.bad && row.level !== 'error').length} warnings · click one for its details` : 'click one for its details'));
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d1d = {
    name: 'rqt_robot_monitor',
    description: 'ROS\'s own diagnostics viewer: Errors / Warnings / All groups, statuses nested by name prefix, and a key / value panel for the selected one. Health only.',
    order: 1.4,
    diagnostics: (node, detail, ctx) => section(node, detail, ctx),
    render(container, detail, ctx) { X().page(container, detail, ctx, 'd1d', section, CSS); },
  };
})(typeof window !== 'undefined' ? window : globalThis);
