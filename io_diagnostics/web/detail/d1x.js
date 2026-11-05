// The shared base of the five Diagnostics variants d1a-d1e (detail/d1a.js ... d1e.js). Loaded from detail/index.json like
// a layout; it registers no render(), so the Prototype picker never lists it.
// WHY: Zach, 2026-10-24: "For the diagnostics view I like the status monitor table.. Super simple. I would like to see 5
// more variants based on the d1 status monitor though", "state and diagnostics could look fairly similar", "I don't like
// here in status how you are showing both the state value AND a diagnostic health thing.. those are separate concerns".
// So every variant keeps d1's page (reason line, children, folded sections, the diagnostic's own page) and changes only
// the Diagnostics SECTION, which shows HEALTH ONLY: name, message, status, and a discrete health history (detail/health.js).
// Values and measurements belong to the State section; they are never drawn here (the diagnostic's own page, one click
// deeper, still has them).
// Rows are organised by the entity page's ONE row model (detail/d6-rows.js, IOEntityRows: folders, pins, A-Z / manual / severity order, the host's
// arrangements.json with localStorage as the fallback); the variants lane's own helper (roworder.js) is gone (Zach, 2026-10-24).
// Each variant also registers `diagnostics(node, detail, ctx)`: that section's HTML alone, for a page that composes its own
// sections (the entity-page lane).
'use strict';
(function (root) {
  const usbText = (text) => (root.IOUsb ? root.IOUsb.label(text) : String(text ?? '')); // a USB link is named by web/usb.js, never "480 Mb/s"
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const rankOf = (level) => (level in RANK ? RANK[level] : -1);
  const HEALTH = () => root.IOHealth;
  const D1 = () => root.IOD1;

  // ------------------------------------------------------------------------------------------ state ----
  // remembered across redraws (the page draws every second) and across entities; keyed by id strings
  const ui = { open: new Map(), sel: new Map() };
  const KEY_SUMMARY = 'io-diagnostics.dx-summary-open';
  try { if (localStorage.getItem(KEY_SUMMARY) === '1') ui.open.set('sum', true); } catch (_) { /* storage blocked */ }
  let last = null;                 // {container, detail, ctx, id, width}: the last draw, so a click can act and draw again
  const wired = new WeakSet();
  const isOpen = (id, byDefault = false) => (ui.open.has(id) ? ui.open.get(id) : byDefault);
  function toggle(id, byDefault = false) {
    ui.open.set(id, !isOpen(id, byDefault));
    if (id === 'sum') { try { localStorage.setItem(KEY_SUMMARY, ui.open.get(id) ? '1' : '0'); } catch (_) { /* storage blocked */ } }
  }

  // ------------------------------------------------------------------------------------------ model ----
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const num = (v) => (Number.isInteger(v) ? String(v) : String(+Number(v).toPrecision(4)));
  const list = (text) => String(text || '').split('\n').map((line) => line.replace(/^\s*\d+[.)]\s*/, '').trim()).filter(Boolean);

  // Every diagnostic of the entity, worst first, then by name (Zach: "sorted by severity, then name").
  function model(node, detail) {
    const rows = ((detail && detail.checks) || []).map((check) => {
      const values = Object.fromEntries((check.values || []).map((v) => [v.key, v.value]));
      const level = check.effective_level;
      return { key: `${node.path}|${check.name}`, id: check.name, rank: rankOf(level), name: check.name, level, bad: PROBLEM.has(level), message: check.message || '', code: check.code || '',
        check, todo: list(values.what_to_check), evidence: values.kernel_evidence || '' };
    }).sort((a, b) => (rankOf(b.level) - rankOf(a.level)) || a.name.localeCompare(b.name));
    // WHY a list of its own: a check that did not run says nothing about the entity, so it is no part of the rows,
    // the problems, the folders' worst level or the status line; the shell (app.js, notRunSplit) keeps it apart in `detail.notRun`.
    // It is shown as one quiet line that opens to its rows, grey, with its own reason, never as OK.
    const notRun = ((detail && detail.notRun) || []).map((check) => ({ key: `${node.path}|${check.name}`, id: check.name, rank: -1, name: check.name, level: check.effective_level, bad: false,
      notRun: true, message: check.message || '', code: check.code || '', check, todo: [], evidence: '' })).sort((a, b) => a.name.localeCompare(b.name));
    return { rows, problems: rows.filter((row) => row.bad), ok: rows.filter((row) => !row.bad), notRun };
  }
  const windowOf = (ctx) => (ctx.settings && Number(ctx.settings.history_window_s)) || 900;
  // The entity's histories (one fetch of /api/v1/history, the page's shared cache): {data, byName}
  function history(node, ctx) {
    const entry = ctx.history(node.path, { everyMs: 4000 });
    const data = entry.data && entry.data.path === node.path ? entry.data : null;
    return { entry, data, byName: new Map(data ? (data.statuses || []).map((status) => [status.name, status]) : []) };
  }

  // ------------------------------------------------------------------------------------ the row order ----
  // Folders ("who: what" and dot-notation names), pins and the sort inside a folder come from the shared row model (d6-rows.js), kept per entity
  // kind and section by the host; State and Configuration build the same trees from the same helper.
  const SEC = 'diagnostics';
  const kindOf = (node) => (node && node.kind) || 'device';
  const ER = () => root.IOEntityRows;
  // rows -> the plan the row model builds: {pinned: [row], root: folder tree, sp, mode}
  function planOf(node, rows, opts = {}) {
    const er = ER();
    const sp = er.spec(kindOf(node), SEC);
    return er.build(rows, { idOf: (row) => row.id, nameOf: (row) => row.name, split: (row) => er.folderPath(row.name), sp, mode: sp.sort || 'severity',
      severityOf: (row) => row.rank, idPrefix: opts.idPrefix || '', tidy: true });
  }
  // the order switch (Severity · A-Z · Manual), as the other tabs draw it
  const toolbar = (node) => `<div class="dx-tb" data-er-section="${SEC}">${ER().sortControl(SEC, ER().spec(kindOf(node), SEC), ['severity', 'name', 'manual'], 'severity')}</div>`;

  // a plan with only its ★ Pinned block (a variant that draws the pins above everything else)
  const pinnedOnly = (plan) => ({ ...plan, root: { type: 'folder', name: '', path: '', children: [] } });

  // The plan drawn: the ★ Pinned block first, then the folders (a button that folds its rows) and the rows, each row through
  // renderRow(info): info = {row, depth, flat (full name), leaf, pinned, drag (attributes), grip, star (html)}.
  function walk(node, plan, renderRow) {
    const er = ER();
    // a folder rolls up the worst level of its rows as ONE icon, never a count (Zach, 2026-10-24)
    const leaves = (folder) => folder.children.flatMap((child) => (child.type === 'leaf' ? [child.item] : leaves(child)));
    const mark = (folder) => {
      const worst = leaves(folder).reduce((w, row) => (row.rank > w.rank ? row : w), { rank: 0 });
      return worst.rank > 0 && last ? `<span class="er-fm">${last.ctx.h.shape(worst.level, worst.level)}</span>` : '';
    };
    return er.renderTree(plan, { section: SEC, sp: plan.sp, mark, leaf: (row, d) => {
      const id = d.id === null ? row.id : d.id;
      return renderRow({ row, depth: d.depth, flat: d.label === null || d.label === row.name, leaf: d.label === null ? row.name : d.label, pinned: d.pinned,
        drag: ` ${er.rowAttrs(d.parent, id, SEC)}`, grip: er.handle(d.parent, id), star: er.star(row.id, d.pinned) });
    } });
  }

  // ---------------------------------------------------------------------------------------- marks ----
  const pill = (ctx, level) => ctx.h.pill(level);
  // a check that did not run: grey and quiet (the plain `none` pill, no warning colour, no data-diagnostic), never an OK, never a problem
  const notRunTag = () => '<span class="pill none dx-nrt" title="This check did not run: it says nothing about the entity">not run</span>';
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const placeholder = (w, text) => `<span class="ioh-none" style="display:inline-block;width:${w}px">${esc(text)}</span>`;

  // One diagnostic's own health history as discrete cells (worst level per bin, from its level spans). Never a value line.
  function cellsOf(row, hist, w, kind, ctx) {
    const H = HEALTH();
    if (!hist.data) return placeholder(w, hist.entry.error ? 'no history' : '…');
    const status = hist.byName.get(row.name);
    if (!status) return placeholder(w, 'no history');
    const n = H.count(hist.data.to - hist.data.from, w, kind);
    return H.html(H.fromLevels(status.levels, hist.data.from, hist.data.to, n), { kind, label: `${row.name}: health over the window` });
  }

  // The status line: the entity's health bar (the API's worst-per-bin rule), its count, and (when expandable) the
  // chevron that drops it down into one lane per diagnostic. Zach: "I like the idea of being able to drop it down to see
  // the status of all the diagnostics".
  function summary(node, m, ctx, hist, opts = {}) {
    const H = HEALTH();
    const W = last ? last.width : 600;
    const barW = W - 14 - 20 - 34;
    const entry = ctx.bar(node.path, H.count(windowOf(ctx), barW, 'timeline'));
    const data = entry.data && entry.data.path === node.path ? entry.data : null;
    const own = data ? data.rows.find((row) => row.depth === 0) : null;
    const worst = m.rows.length ? m.rows[0].level : null;
    // a rolled-up line shows only the highest value of what it rolls up, as its icon, never a count (Zach, 2026-10-24: "the number
    // next to the warning count here is misleading ... all we should show is just the highest value of any of the children and
    // its just the icon"); all OK is a quiet word, not an icon
    const count = !m.rows.length ? '' : m.problems.length ? ctx.h.shape(worst, worst) : '<span class="dx-okw">OK</span>';
    const bar = own ? H.html(H.fromBar(own.bar, data.from, data.bin_s), { kind: 'timeline', label: 'health over the window' })
      : placeholder(barW, entry.error ? `no history: ${entry.error}` : 'history loading…');
    const open = opts.expandable !== false && isOpen('sum');
    const basis = !own || own.basis === 'none' ? 'nothing in the history yet' : own.basis === 'below' ? 'everything below' : 'its own diagnostics';
    const axisText = data ? `${basis} · worst per ${H.span(data.bin_s)}` : '';
    const line = opts.expandable === false
      ? `<div class="dx-sumline plain"><span class="dx-bar">${bar}</span><span class="dx-count">${count}</span></div>`
      : `<button class="dx-sumline" data-dx-toggle="sum" aria-expanded="${open}" title="${open ? 'Fold the diagnostics lanes' : 'Drop down one lane per diagnostic'}">`
        + `<svg class="ic dx-chev${open ? ' open' : ''}"><use href="#i-chev"/></svg><span class="dx-bar">${bar}</span><span class="dx-count">${count}</span></button>`;
    return `<div class="dx-sum${open ? ' open' : ''}">${line}`
      + (data && !opts.noAxis ? `<div class="dx-sumaxis${opts.expandable === false ? ' plain' : ''}">${H.axis(data, axisText)}</div>` : '')
      + (open && m.rows.length ? `<div class="dx-drop">${lanes(node, m, ctx, hist, { select: false })}</div>` : '') + '</div>';
  }

  // One lane per diagnostic: name · its timeline across the whole window · current status, in the folders and order of the
  // row-order helper. Used by the status line's drop-down and by d1c (where a click selects the lane).
  function lanes(node, m, ctx, hist, opts = {}) {
    const H = HEALTH();
    const W = last ? last.width : 600;
    const nameW = opts.nameW || Math.min(220, Math.round(W * 0.3));
    const statusW = 76;
    const laneW = W - nameW - statusW - 20 - (opts.reserve || 0);
    const selected = opts.selected || null;
    const plan = opts.plan || planOf(node, m.rows);
    const rows = walk(node, plan, (r) => {
      const row = r.row;
      // embedded in another page (the entity page's Diagnostics tab) there is no diagnostic page to open: a lane is a plain line
      const attr = opts.select ? `data-dx-select="${esc(row.name)}"` : last && last.embedded ? '' : `data-dx-open="${esc(row.name)}"`;
      const shown = r.flat === false ? esc(r.leaf) : D1().nameHtml(row.name, ctx.h);
      return `<div class="dx-lane${row.bad ? ' bad' : ''}${selected === row.name ? ' sel' : ''}" style="--depth:${r.depth}" ${attr}${r.drag} role="button" tabindex="0" title="${esc(row.name)} — ${esc(row.message)}">`
        + `<span class="dx-lname">${r.grip}${r.star}<span class="dx-nt">${shown}</span></span>${cellsOf(row, hist, laneW, 'lane', ctx)}`
        + `<span class="dx-lst">${pill(ctx, row.level)}</span></div>${opts.after ? opts.after(row) : ''}`;
    });
    return `<div class="dx-lanes" style="--dx-name:${nameW}px;--dx-status:${statusW}px">${rows}</div>`;
  }

  // What a row opens to, health only: what to check (numbered), the full message, the quiet timing line, the way on.
  function body(row, ctx, opts = {}) {
    const h = ctx.h;
    const check = row.check;
    const caused = (check.caused_by || []).map((path) => {
      const n = ctx.byPath.get(path);
      return `<a class="link" data-go="${esc(path)}">${esc(n ? n.name : path)}</a>`;
    }).join(', ');
    const timing = check.expected_period_s ? `every ${num(check.expected_period_s)} s · STALE after ${num(check.stale_after_s)} s` : 'event-driven (never stale)';
    const inner = `<div class="dx-full">${esc(usbText(row.message || '—'))}${row.code ? ` <span class="mono dx-code">${esc(row.code)}</span>` : ''}</div>`
      + (row.bad && row.todo.length ? `<div class="dx-todo-h">What to check</div><ol class="dx-todo">${row.todo.map((line) => `<li>${esc(line)}</li>`).join('')}</ol>` : '')
      + (row.bad && row.evidence ? `<div class="dx-ev">kernel evidence: ${esc(row.evidence)}</div>` : '')
      + `<div class="dx-meta">received ${esc((check.received_iso || '').slice(11, 19) || '—')} · ${h.ago(check.age_s)} ago · ${esc(timing)}`
      + `${check.level !== check.effective_level ? ` · reported ${esc(check.level)}` : ''}${caused ? `<br>caused by ${caused}` : ''}<br>${esc(check.source || '')}</div>`
      + (last && last.embedded ? '' : `<div class="dx-more"><a class="link" data-dx-open="${esc(row.name)}" tabindex="0">Open this diagnostic →</a></div>`);
    return opts.bare ? inner : `<div class="dx-x">${inner}</div>`;
  }

  // A one-line table row (name · message · status · health), used by d1a, d1b's OK list and d1d.
  function line(row, ctx, hist, opts = {}) {
    const open = isOpen(`row:${row.key}`);
    const r = opts.r || { depth: 0, flat: true, drag: '', grip: '', star: '' };
    const hooks = (last && last.hooks) || null;   // the embedding page's own marks (the entity page: a fault's meaning, its log link, a stock diagnostic's drill-in)
    const shown = r.flat === false ? esc(r.leaf) : D1().nameHtml(row.name, ctx.h);
    return `<div class="dx-r${row.bad ? ' bad' : ''}${open ? ' open' : ''}${hooks ? hooks.rowClass(row) : ''}" style="--depth:${r.depth}"${r.drag}${row.bad ? ' data-diagnostic' : ''}${hooks ? ` data-code="${esc(row.code)}"` : ''}>`
      + `<div class="dx-line" data-dx-toggle="row:${esc(row.key)}" role="button" tabindex="0" aria-expanded="${open}" title="${esc(row.name)} — ${esc(row.message)}${row.code ? ` · ${esc(row.code)}` : ''}">`
      + `<span class="dx-n">${r.grip}${r.star}${hooks && hooks.lead ? hooks.lead(row, open) : ''}<span class="dx-nt">${shown}</span></span><span class="dx-m"><span class="dx-mt">${esc(usbText(row.message || '—'))}</span>${hooks ? hooks.extra(row) : ''}</span>`
      + `<span class="dx-s">${row.notRun ? notRunTag() : pill(ctx, row.level)}</span>${opts.spark === false ? '' : `<span class="dx-h">${cellsOf(row, hist, opts.sparkW || 96, 'spark', ctx)}</span>`}</div>`
      + `${open ? body(row, ctx) + (hooks && hooks.body ? hooks.body(row) : '') : ''}</div>`;
  }
  const lineStyle = (W, opts = {}) => `--dx-name:${opts.nameW || Math.min(240, Math.round(W * 0.32))}px;--dx-spark:${opts.sparkW || 96}px`;

  // -------------------------------------------------------------------------------------- the page ----
  const FOLDS = (ctx, node, detail) => {
    const p = ctx.parts;
    const D = D1();
    return D.fold(ctx, 'faults', `Faults · ${detail.faults.length}`, p.faults())
      + D.fold(ctx, 'events', `Events · ${detail.events.length}`, detail.events.length ? p.events() : '')
      + D.fold(ctx, 'links', 'Link health', p.links(), 'the computer diagnoses its own wires')
      + D.fold(ctx, 'setup', 'Expected setup', p.setup(), 'the rig file, never the device')
      + D.fold(ctx, 'status', 'Rollup', p.status())
      + D.fold(ctx, 'identity', 'Identity', p.identity())
      + D.fold(ctx, 'connection', `Connection · ${ctx.counts.connection}`, p.connection(), 'the layers this row is reached through');
  };

  // A variant's section, with d1's header: `inner(node, detail, ctx, m, hist)` is its body, `right` the header's hint.
  function section(node, detail, ctx, inner, right) {
    const m = model(node, detail);
    const hist = history(node, ctx);
    const content = (m.rows.length ? inner(m, hist) : `<div class="muted">${m.notRun.length ? 'No diagnostics of its own ran.' : 'No diagnostics of its own.'}</div>`) + notRunLine(m, ctx, hist);
    return D1().plain('dx-diag', `Diagnostics · ${m.rows.length}`, content, right(m));
  }

  // "N checks did not run": one quiet line under the table, opening to the rows (each with its own reason). WHY a line and not a grey row
  // each: a board that cannot feed twenty bindings would otherwise list twenty rows that say nothing about it.
  function notRunLine(m, ctx, hist) {
    const n = m.notRun.length;
    if (!n) return '';
    const open = isOpen('notrun');
    const W = last ? last.width : 600;
    return `<div class="dx-nr" style="${lineStyle(W)}" data-dx-notrun><button class="dx-nr-h" data-dx-toggle="notrun" aria-expanded="${open}" title="${open ? 'Fold these checks' : 'Show each check that did not run, with its reason'}">`
      + `<svg class="ic dx-chev${open ? ' open' : ''}"><use href="#i-chev"/></svg><span>${n} check${n === 1 ? '' : 's'} did not run</span></button>`
      + (open ? `<div class="dx-nr-rows">${m.notRun.map((row) => line(row, ctx, hist, { spark: false })).join('')}</div>` : '') + '</div>';
  }

  // the host's arrangements (GET /api/v1/arrangements), once; the page redraws when they arrive
  function loadRows() {
    const er = ER();
    if (er && !er.arrangement.started) er.loadArrangements(() => { if (last) last.ctx.redraw(); });
  }

  function injectCss() {
    if (typeof document === 'undefined' || document.querySelector('style[data-layout="d1x"]')) return;
    const style = document.createElement('style');
    style.dataset.layout = 'd1x';
    style.textContent = CSS;
    document.head.appendChild(style);
  }

  // The whole entity page of a variant. A group row, a loading detail and the diagnostic's own page are d1's (the page the
  // variants are variants of); the entity page is d1's with the Diagnostics section swapped.
  function page(container, detail, ctx, id, makeSection, extraCss) {
    injectCss();
    D1().injectCss();
    if (extraCss && !document.querySelector(`style[data-layout="${id}"]`)) {
      const style = document.createElement('style');
      style.dataset.layout = id;
      style.textContent = extraCss;
      document.head.appendChild(style);
    }
    wire(container);
    loadRows();
    const d1 = root.IODetailLayouts && root.IODetailLayouts.d1;
    const node = ctx.node;
    const open = ctx.diagnostic && node && ctx.diagnostic.path === node.path ? ctx.diagnostic : null;
    if (ctx.group || !detail || open) { last = null; d1.render(container, detail, ctx); return; }
    last = { container, detail, ctx, id, width: HEALTH().widthOf(container) };
    const p = ctx.parts;
    const D = D1();
    const kids = (node.children || []).length;
    const checks = detail.checks.map((check, at) => ({ check, at }))
      .sort((a, b) => (rankOf(b.check.effective_level) - rankOf(a.check.effective_level)) || (a.at - b.at)).map(({ check }) => check);
    ctx.draw(`${p.head()}${p.banners()}<div class="d-body" data-dx="${id}" data-dx-variant="${id}">`
      + D.rollsUp(node, checks, ctx)
      + makeSection(node, detail, ctx)
      + D.plain('d1-children', `Below · ${kids}`, D.childrenList(node, ctx), 'each row\'s own health over the same window')
      + FOLDS(ctx, node, detail)
      + '</div>');
  }

  // A variant's section inside ANOTHER page (the entity page's Diagnostics tab draws d1a this way): sets up the CSS, the clicks and
  // the width for `container`, and returns the section's HTML wrapped in the hook the clicks look for. There is no diagnostic page to
  // open there, so the "Open this diagnostic" links are left out.
  function embed(container, detail, ctx, id, makeSection, extraCss, hooks) {
    injectCss();
    D1().injectCss();
    if (extraCss && !document.querySelector(`style[data-layout="${id}"]`)) {
      const style = document.createElement('style');
      style.dataset.layout = id;
      style.textContent = extraCss;
      document.head.appendChild(style);
    }
    wire(container);
    loadRows();
    last = { container, detail, ctx, id, width: HEALTH().widthOf(container), embedded: true, hooks: hooks || null };
    return `<div data-dx="${id}" data-dx-variant="${id}" data-dx-embedded>${makeSection(ctx.node, detail, ctx)}</div>`;
  }

  // ---------------------------------------------------------------------------------------- the CSS ----
  const CSS = `
.dx-sumline { display: grid; grid-template-columns: 14px minmax(0, 1fr) auto; gap: 10px; align-items: center; width: 100%; padding: 4px 0; border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; border-radius: var(--io-radius-sm); }
.dx-sumline.plain { grid-template-columns: minmax(0, 1fr) auto; cursor: default; }
button.dx-sumline:hover { background: var(--io-hover); }
button.dx-sumline:focus-visible, .dx-line:focus-visible, .dx-lane:focus-visible, [data-dx-open]:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.dx-chev { width: 12px; height: 12px; color: var(--io-faint); transition: transform .12s; }
.dx-chev.open { transform: rotate(90deg); }
.dx-bar { min-width: 0; display: block; }
.dx-count { white-space: nowrap; font-size: 12px; color: var(--io-muted); display: inline-flex; align-items: center; min-width: 34px; justify-content: flex-end; }
.dx-count .shape { width: 14px; height: 14px; }
.dx-okw { color: var(--io-faint); font-size: 11px; letter-spacing: .3px; }
.dx-sumaxis { padding: 0 44px 0 24px; }
.dx-sumaxis.plain { padding-left: 0; }
.dx-drop { margin: 8px 0 2px; padding: 6px 0 2px; border-top: 1px solid var(--io-border-soft); }
.dx-lanes { display: grid; gap: 4px; }
.dx-lane { display: grid; grid-template-columns: var(--dx-name) minmax(0, 1fr) var(--dx-status); gap: 10px; align-items: center; padding: 1px 0; border-radius: var(--io-radius-sm); cursor: pointer; }
.dx-lane:hover, .dx-lane.sel { background: var(--io-hover); }
.dx-lane.sel { box-shadow: inset 2px 0 0 var(--io-selected-line); }
.dx-lname { font-size: 12px; color: var(--io-muted); }
.dx-lane.bad .dx-lname { color: var(--io-text-strong); font-weight: 600; }
.dx-lst { display: inline-flex; justify-content: flex-end; }
.dx-lst .pill { min-width: 60px; justify-content: center; }
.dx-hd { display: grid; grid-template-columns: var(--dx-name) minmax(0, 1fr) 76px var(--dx-spark); gap: 10px; padding: 2px 4px 4px; color: var(--io-faint); font-size: 11px; border-bottom: 1px solid var(--io-border-soft); }
.dx-r { border-bottom: 1px solid var(--io-border-soft); }
.dx-line { display: grid; grid-template-columns: var(--dx-name) minmax(0, 1fr) 76px var(--dx-spark); gap: 10px; align-items: center; min-height: 28px; padding: 3px 4px; border-radius: var(--io-radius-sm); cursor: pointer; font-size: 12.5px; }
.dx-line:hover { background: var(--io-hover); }
.dx-m { display: flex; align-items: baseline; gap: 6px; min-width: 0; overflow: hidden; white-space: nowrap; }
.dx-mt { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.dx-m > :not(.dx-mt) { flex: none; }
.dx-n, .dx-lname { display: flex; align-items: center; gap: 2px; min-width: 0; padding-left: calc(var(--depth, 0) * 14px); }
.dx-nt { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dx-n { color: var(--io-muted); }
.dx-r.bad .dx-n { color: var(--io-text-strong); font-weight: 600; }
.dx-m { color: var(--io-muted); }
.dx-r.bad .dx-m { color: var(--io-text); }
.dx-s { display: inline-flex; justify-content: flex-start; }
.dx-s .pill { min-width: 60px; justify-content: center; }
.dx-h { display: block; min-width: 0; }
.dx-x { padding: 2px 4px 10px 4px; display: grid; gap: 8px; min-width: 0; font-size: 12.5px; }
.dx-full { overflow-wrap: anywhere; color: var(--io-text); }
.dx-code { color: var(--io-faint); font-size: 11px; }
.dx-todo-h { font-size: 11px; text-transform: uppercase; letter-spacing: .5px; color: var(--io-muted); font-weight: 600; margin-top: 2px; }
.dx-todo { margin: 0; padding-left: 20px; display: grid; gap: 3px; }
.dx-todo li { overflow-wrap: anywhere; }
.dx-ev { color: var(--io-muted); font-size: 12px; }
.dx-meta { color: var(--io-faint); font-size: 11px; overflow-wrap: anywhere; }
.dx-more { font-size: 12px; }
.sec.dx-diag .sec-b { padding-top: 0; }
.dx-nr { margin-top: 10px; }
.dx-nr-h { display: inline-flex; align-items: center; gap: 6px; padding: 2px 4px; border: 0; background: none; color: var(--io-faint); font: inherit; font-size: 12px; cursor: pointer; border-radius: var(--io-radius-sm); }
.dx-nr-h:hover { background: var(--io-hover); color: var(--io-muted); }
.dx-nr-h:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.dx-nr-rows { margin-top: 4px; }
.dx-nr-rows .dx-n, .dx-nr-rows .dx-m { color: var(--io-faint); font-weight: 400; }
`;

  // ---------------------------------------------------------------------------------------- clicks ----
  // Only this base's own hooks (data-dx-*); data-go, data-util and the rest stay the page's.
  function wire(container) {
    if (wired.has(container)) return;
    wired.add(container);
    const act = (target) => {
      if (!last || !container.querySelector('[data-dx]')) return false; // another layout draws the panel now
      const openEl = target.closest('[data-dx-open]');
      const selectEl = target.closest('[data-dx-select]');
      const toggleEl = target.closest('[data-dx-toggle]');
      const path = last.ctx.node && last.ctx.node.path;
      if (openEl && path) { last.ctx.openDiagnostic(openEl.dataset.dxOpen); return true; }
      if (selectEl && path) {
        const name = selectEl.dataset.dxSelect;
        ui.sel.set(path, ui.sel.get(path) === name ? '' : name); // '' = the viewer folded the selection away
        last.ctx.redraw();
        return true;
      }
      if (toggleEl) { toggle(toggleEl.dataset.dxToggle, toggleEl.hasAttribute('data-dx-default-open')); last.ctx.redraw(); return true; }
      return false;
    };
    // WHY only when not embedded: the entity page (d6) routes the row model's clicks, changes and drags for everything inside its container
    const rows = () => (last && !last.embedded && container.querySelector('[data-dx]') ? { kind: kindOf(last.ctx.node), redraw: last.ctx.redraw } : null);
    container.addEventListener('click', (event) => {
      const target = event.target;
      if (!(target instanceof Element) || target.closest('.ent-tools, [data-util], .u-panel, input, select, textarea, a[data-go], [data-d6-jump]')) return;
      const mine = rows();
      if (mine && ER().onClick(target, mine)) return;
      act(target);
    });
    container.addEventListener('change', (event) => { const mine = rows(); if (mine && event.target instanceof Element) ER().onChange(event.target, mine); });
    container.addEventListener('dragstart', (event) => { const mine = rows(); if (mine) ER().onDragStart(event, mine); });
    container.addEventListener('dragover', (event) => { if (rows()) ER().onDragOver(event); });
    container.addEventListener('drop', (event) => { const mine = rows(); if (mine) ER().onDrop(event, mine, container); });
    container.addEventListener('dragend', () => { if (rows()) ER().clearDrag(container); });
    container.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const target = event.target;
      if (!(target instanceof Element) || !target.matches('[data-dx-open], [data-dx-select], [data-dx-toggle]')) return;
      if (act(target)) event.preventDefault();
    });
  }

  root.IODiagX = { RANK, PROBLEM, rankOf, esc, num, ui, isOpen, toggle, model, notRunLine, history, windowOf, pill, plural, placeholder, cellsOf, summary, lanes, body, line, lineStyle, section, page, planOf, pinnedOnly, walk, toolbar, kindOf, embed, injectCss, last: () => last };
})(typeof window !== 'undefined' ? window : globalThis);
