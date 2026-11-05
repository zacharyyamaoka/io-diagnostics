// Tree proposal v5 — "Columns tree": the tree is also the table.
//
// One nested first column (type icon + name), then quiet aligned columns the way Clearpath's "All Diagnostics" and
// NVIDIA Greenwave Monitor lay them out: PORT · ID · RATE (measured / expected) · LAST SEEN · FIRMWARE, and the
// status shape + message in the LAST column, drawn only when the row is not OK.
//
// Contract: docs/api-v1.md only. Exposes window.IOTreeRenderers.v5 = {name, render(container, apiTree, opts)},
// opts = {onSelect(path), selectedPath}. The renderer keeps its own expand/collapse state per container and patches
// rows in place, so the page may call render() on every 1 s tick.
//
// Zach's tree rules (2026-10-22), and where each one lives in this file:
//   1. chevrons only on hover, Notion-style OVER the type icon — never a permanent column        → nameCell()
//   2. type icon + name together; the status icon moves to the RIGHT (last column)               → statusCell()
//   3. problems are SHAPES: ▲ warn, octagon ✖ error, clock stale, ? unknown                      → SHAPES
//   4. quiet when OK: a healthy row draws no status at all, no green anywhere                     → statusCell()
//   5. not connected = fully greyed; connected = normal grey text; only problems get colour      → rowHtml()
//   6. black box: a row shows its OWN level; the worst-below rollup is only a quiet hint          → rollupHint()
(function () {
  'use strict';

  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const INDENT_PX = 14;
  const DETAIL_TTL_MS = 5000;     // re-read a row's /api/v1/node at most this often
  const DETAIL_RETRY_MS = 30000;  // after a failed read (static page, host gone) wait this long

  // Type icons: the same lucide-style vocabulary as web/index.html, keyed by the API's closed `icon` set.
  // Inlined (not <use> of the page's sprite) so the renderer stands alone on any page.
  const TYPE_ICONS = {
    host: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
    group: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
    usb: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>',
    pci: '<rect x="3" y="6" width="18" height="10" rx="1.5"/><path d="M6 16v3M9 16v3M12 16v3M15 16v3M18 16v3M7 10h4"/>',
    net: '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-3h14v3"/>',
    tty: '<rect x="2" y="4" width="20" height="16" rx="2"/><path d="m6 9 3 3-3 3M11 15h5"/>',
    video: '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="m16 10 6-3v10l-6-3"/>',
    port: '<rect x="3" y="7" width="18" height="10" rx="1.5"/><path d="M7 11h3M14 11h3"/>',
    hub: '<rect x="16" y="16" width="6" height="6" rx="1"/><rect x="2" y="16" width="6" height="6" rx="1"/><rect x="9" y="2" width="6" height="6" rx="1"/><path d="M5 16v-3a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1v3M12 12V8"/>',
    adapter: '<path d="M12 22v-5M9 8V2M15 8V2M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z"/>',
    bus: '<path d="M3 17h18M6 17v-6M12 17v-6M18 17v-6"/><circle cx="6" cy="8" r="2"/><circle cx="12" cy="8" r="2"/><circle cx="18" cy="8" r="2"/>',
    servo: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2.5"/><path d="M12 4v3M20 12h-3M12 20v-3M4 12h3"/>',
    chip: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
    motor: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    camera: '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/><circle cx="12" cy="13" r="3"/>',
    stream: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    webcam: '<circle cx="12" cy="10" r="8"/><circle cx="12" cy="10" r="3"/><path d="M7 22h10M12 22v-4"/>',
    hid: '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M6 9h.01M10 9h.01M14 9h.01M18 9h.01M8 13h.01M12 13h.01M16 13h.01M7 16h10"/>',
    device: '<rect x="4" y="4" width="16" height="16" rx="3"/><circle cx="12" cy="12" r="2"/>',
  };

  // Rule 3: a problem is read by its SHAPE first, colour second (colour-blind safe, greyscale safe).
  // `.v5-fill` gets a translucent tint of the level colour so the silhouette reads at 14 px.
  const SHAPES = {
    warn: '<path class="v5-fill" d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9.5v3.5M12 17h.01"/>',
    error: '<path class="v5-fill" d="M7.86 2h8.28L22 7.86v8.28L16.14 22H7.86L2 16.14V7.86Z"/><path d="m15 9-6 6M9 9l6 6"/>',
    stale: '<circle class="v5-fill" cx="12" cy="12" r="10"/><path d="M12 6.5V12l3.5 2"/>',
    unknown: '<circle class="v5-fill" cx="12" cy="12" r="10"/><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01"/>',
  };
  const CHEVRON = '<path d="m9 6 6 6-6 6"/>';
  const LEVEL_WORD = { warn: 'warning', error: 'error', stale: 'stale', unknown: 'unknown' };

  // ------------------------------------------------------------------------------- helpers ----
  const esc = (text) => String(text ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const svg = (paths, cls) => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${paths}</svg>`;
  const typeIcon = (name) => svg(TYPE_ICONS[name] || TYPE_ICONS.device, 'v5-ic');
  const shape = (level, extra = '') => (SHAPES[level] ? svg(SHAPES[level], `v5-shape v5-l-${level} ${extra}`) : '');
  const ownLevel = (node) => (node.own && 'level' in node.own ? node.own.level : node.level);
  const isProblem = (level) => Boolean(level) && level !== 'ok';

  // Compact units ("2s", "11m") so "age / period" fits a quiet 80 px column; the tooltip spells it out.
  function fmtAge(seconds) {
    if (seconds === null || seconds === undefined) return '';
    if (seconds < 90) return `${Math.round(seconds)}s`;
    if (seconds < 5400) return `${+(seconds / 60).toFixed(seconds < 600 ? 1 : 0)}m`;
    return `${+(seconds / 3600).toFixed(1)}h`;
  }
  function fmtPeriod(seconds) {
    if (!seconds) return '';
    return seconds < 90 ? `${+seconds.toFixed(1)}s` : `${+(seconds / 60).toFixed(1)}m`;
  }
  const spell = (text) => text.replace(/(\d)s$/, '$1 s').replace(/(\d)m$/, '$1 min').replace(/(\d)h$/, '$1 h');
  // measured: one decimal always ("30.0", so a column of rates reads evenly); expected: as declared ("30")
  const fmtHz = (value, measured) => (typeof value !== 'number' ? esc(value)
    : measured ? value.toFixed(value < 100 ? 1 : 0) : String(+value.toFixed(1)));
  // "Port 7-1.1.4" → "7-1.1.4" for the PORT column (the column header already says "port"); the authored name is
  // the cell's tooltip one hover away, and any name that does not start with "Port " is shown whole.
  const shortPort = (name) => String(name || '').replace(/^Port\s+/, '');

  // ------------------------------------------------------------------- per-container state ----
  const STATES = new WeakMap();
  function stateFor(container) {
    let st = STATES.get(container);
    if (!st) {
      st = { open: new Map(), tree: null, opts: {}, byPath: new Map(), rows: [], keys: [], htmls: [], headHtml: '',
             root: null, head: null, body: null, lastSel: undefined, localSel: null, pending: 0 };
      STATES.set(container, st);
    }
    return st;
  }

  // Default expansion: open the skeleton (host, non-empty buses, ports, hubs) and the way to anything worse below;
  // a device that is itself the problem stays closed (its own row already says so).
  function defaultOpen(node) {
    if (node.kind === 'host' || node.kind === 'group') return true;
    if (node.kind === 'bus' && node.depth <= 1) return node.children.length > 0;
    if (node.kind === 'port' || node.icon === 'hub') return true;
    if (node.hint) return true;
    return ownLevel(node) === null && isProblem(node.level);
  }
  const isOpen = (st, node) => (st.open.has(node.path) ? st.open.get(node.path) : defaultOpen(node));

  // A quiet port (own OK, one device, connected, no open faults) folds into its device's row: the port becomes the
  // PORT column instead of a whole tree level. WHY: "which USB port is it in" is a column question (Phoenix Tuner's
  // Bus column), and folding drops one indent level per hop, so a hub chain no longer marches off the right edge.
  // A port with its own problem, or an empty one, stays a row of its own: nothing that reports trouble is hidden.
  function foldsIntoChild(node) {
    return node.kind === 'port' && node.connected !== false && node.children.length === 1
      && !isProblem(ownLevel(node)) && !node.knock_on_of && !node.faults;
  }

  function indexTree(tree) {
    const byPath = new Map();
    (function visit(node) { byPath.set(node.path, node); (node.children || []).forEach(visit); })(tree);
    return byPath;
  }

  function visibleRows(st, tree) {
    const rows = [];
    (function walk(node, depth) {
      let port = null;
      let shown = node;
      while (foldsIntoChild(shown)) { port = shown; shown = shown.children[0]; }
      rows.push({ node: shown, port, depth });
      if (shown.children.length && isOpen(st, shown)) shown.children.forEach((child) => walk(child, depth + 1));
    })(tree, 0);
    return rows;
  }

  // Reveal a selection made elsewhere (the chip, the attention list, a detail-panel link): open its ancestors once.
  function revealSelection(st, path) {
    let node = st.byPath.get(path);
    while (node && node.parent) {
      st.open.set(node.parent, true);
      node = st.byPath.get(node.parent);
    }
  }

  // ------------------------------------------------- optional detail: rate + update-available ----
  // The tree node carries `expected_period_s`, `age_s` and `identity.sw_version`, but a measured frequency and the
  // firmware check's UpdateAvailable/latest live in /api/v1/node. Rows that can carry them (periodic rows, rows with a
  // sw_version, streams) are read lazily, one GET per row per DETAIL_TTL_MS; a page without the API (a static
  // snapshot) simply leaves those cells empty.
  const DETAIL = new Map();
  const wantsDetail = (node) => node.checks > 0
    && Boolean(node.expected_period_s || (node.identity && node.identity.sw_version) || node.kind === 'stream');

  function digestDetail(data) {
    const hz = [];
    for (const check of data.checks || []) {
      for (const m of check.measurements || []) {
        if (m.unit === 'Hz' && typeof m.value === 'number') hz.push({ ...m, check: check.name });
      }
    }
    // intel_rgbd sends the configured rate as an `expected_rate` measurement; anything else in Hz is a measured rate
    const expected = hz.find((m) => /^expected/i.test(m.key)) || null;
    const measured = hz.find((m) => !/^expected/i.test(m.key)) || null;
    const firmware = (data.checks || []).find((c) => /(^|[:\s])firmware$/i.test(c.name));
    let fw = null;
    if (firmware) {
      const value = (key) => ((firmware.values || []).find((v) => v.key === key) || {}).value;
      fw = { update: firmware.code === 'UpdateAvailable', installed: value('installed_version'), latest: value('latest_version'),
             level: firmware.effective_level || firmware.level, message: firmware.message };
    }
    return { measured, expected, fw };
  }

  function requestDetail(path, container) {
    const now = Date.now();
    const entry = DETAIL.get(path) || {};
    if (entry.pending || (entry.at && now - entry.at < DETAIL_TTL_MS) || (entry.failedAt && now - entry.failedAt < DETAIL_RETRY_MS)) return;
    entry.pending = true;
    DETAIL.set(path, entry);
    fetch(`/api/v1/node?path=${encodeURIComponent(path)}`, { signal: AbortSignal.timeout(8000) })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error(String(response.status)))))
      .then((data) => {
        DETAIL.set(path, { at: Date.now(), data: digestDetail(data) });
        scheduleRepaint(container);
      })
      .catch(() => { DETAIL.set(path, { ...entry, pending: false, failedAt: Date.now() }); });
  }

  function scheduleRepaint(container) {
    const st = stateFor(container);
    if (st.pending) return;
    // setTimeout, not requestAnimationFrame: rAF never fires in a hidden tab, and a batch of replies lands together
    st.pending = setTimeout(() => { st.pending = 0; if (st.tree) paint(container, st); }, 40);
  }

  // ---------------------------------------------------------------------------------- cells ----
  function nameCell(row, st) {
    const node = row.node;
    const hasKids = node.children.length > 0;
    const open = hasKids && isOpen(st, node);
    // Rule 1: the chevron sits in the icon's own box and swaps in on hover (CSS), so no column is spent on it
    const twisty = hasKids
      ? `<span class="v5-twisty has ${open ? 'open' : ''}" data-v5-toggle="${esc(node.path)}" title="${open ? 'Collapse' : 'Expand'}">${typeIcon(node.icon)}${svg(CHEVRON, 'v5-chev')}</span>`
      : `<span class="v5-twisty">${typeIcon(node.icon)}</span>`;
    const part = node.edge === 'part_of' ? ' · part of its parent' : '';
    const title = `${node.name}${node.message ? ` — ${node.message}` : ''}\n${node.path}${part}`;
    return `<div class="v5-c v5-c-name" style="padding-left:${8 + row.depth * INDENT_PX}px" title="${esc(title)}">${twisty}<span class="v5-name">${esc(node.name)}</span></div>`;
  }

  function portCell(row) {
    if (!row.port) return '<div class="v5-c v5-c-port"></div>';
    const port = row.port;
    const speed = port.id_text ? ` · ${port.id_text}` : '';
    return `<div class="v5-c v5-c-port"><span class="v5-port" data-v5-select="${esc(port.path)}" title="${esc(`${port.name}${speed} — click to open the port`)}">${esc(shortPort(port.name))}</span></div>`;
  }

  function rateCell(detail) {
    const empty = '<div class="v5-c v5-c-rate"></div>';
    if (!detail || (!detail.measured && !detail.expected)) return empty;
    const m = detail.measured;
    const e = detail.expected;
    const level = m && isProblem(m.level) ? m.level : null;
    const limits = m ? ['warn_low', 'warn_high', 'error_low', 'error_high'].filter((k) => m[k] !== null && m[k] !== undefined)
      .map((k) => `${k.replace('_', ' ')} ${m[k]}`).join(', ') : '';
    const title = [m ? `measured ${m.text || `${m.value} Hz`}${limits ? ` (${limits})` : ''} · ${m.check}` : 'no measured rate reported',
                   e ? `expected ${e.text || `${e.value} Hz`} · ${e.check}` : 'no expected rate reported'].join('\n');
    return `<div class="v5-c v5-c-rate" title="${esc(title)}"><span class="${level ? `v5-t-${level}` : 'v5-val'}">${m ? fmtHz(m.value, true) : '—'}</span>`
      + `<span class="v5-dim">${e ? ` / ${fmtHz(e.value, false)}` : ''} Hz</span></div>`;
  }

  function seenCell(node) {
    // Only periodic rows have a "should have heard from it by now": event-driven rows (presence, plug) never go
    // stale, so their age is not health and the cell stays empty (rule 4).
    if (!node.expected_period_s || node.age_s === null || node.age_s === undefined) return '<div class="v5-c v5-c-seen"></div>';
    const title = `last periodic report ${spell(fmtAge(node.age_s))} ago · expected every ${spell(fmtPeriod(node.expected_period_s))}`
      + (node.stale_after_s ? ` · STALE after ${spell(fmtPeriod(node.stale_after_s))}` : '');
    // overdue by the host's own threshold, even when a worse own level (an error) outranks STALE on the row
    const overdue = node.stale || (node.stale_after_s && node.age_s > node.stale_after_s);
    return `<div class="v5-c v5-c-seen" title="${esc(title)}"><span class="${overdue ? 'v5-t-stale' : 'v5-val'}">${fmtAge(node.age_s)}</span>`
      + `<span class="v5-dim"> / ${fmtPeriod(node.expected_period_s)}</span></div>`;
  }

  function firmwareCell(node, detail) {
    const sw = node.identity && node.identity.sw_version;
    const fw = detail && detail.fw;
    if (fw && fw.update && fw.latest) {
      const installed = fw.installed || sw || '?';
      const title = `installed ${installed} · ${fw.latest} available${sw && sw !== installed ? `\nsw_version: ${sw}` : ''}${fw.message ? `\n${fw.message}` : ''}`;
      return `<div class="v5-c v5-c-fw" title="${esc(title)}"><span class="v5-val">${esc(installed)}</span><span class="v5-upd"> → ${esc(fw.latest)}</span></div>`;
    }
    if (!sw) return '<div class="v5-c v5-c-fw"></div>';
    const current = fw && fw.latest && !fw.update ? `\nlatest ${fw.latest}: up to date` : '';
    return `<div class="v5-c v5-c-fw" title="${esc(sw + current)}"><span class="v5-val">${esc(sw)}</span></div>`;
  }

  // Rule 6: the rollup is a hint, drawn small and faint, and only while the branch is CLOSED. Open, the problem row
  // itself is on screen one or two lines down, so repeating it on every ancestor would be the busy V6 red chain.
  // A pure grouping row (own.level null: the host, a bus) has no self-report, so its worst-below is a hint too.
  function rollupHint(node, st) {
    if (!node.children.length || isOpen(st, node)) return null;
    const rollup = node.rollup || {};
    const level = node.hint ? node.hint.level : (ownLevel(node) === null && isProblem(rollup.level) ? rollup.level : null);
    if (!level || !SHAPES[level]) return null;
    const who = rollup.origin_name || (node.hint && node.hint.origin) || '';
    // WHY no count (Zach, 2026-10-24: "when we roll up all we should show is just the highest value of any of the children")
    const text = `below · ${who}`;
    return { level, text, title: (node.hint && node.hint.message) || rollup.message || text };
  }

  function statusCell(node, st) {
    const own = ownLevel(node);
    let html = '';
    let title = '';
    if (isProblem(own)) {
      if (node.knock_on_of) {
        // explained by a failed ancestor: one cause, counted once; drawn subdued, in grey, never in colour
        const cause = st.byPath.get(node.knock_on_of);
        const causeName = cause ? cause.name : node.knock_on_of;
        html += `${shape(own, 'v5-knock')}<span class="v5-msg v5-knock-t">${esc(node.message || LEVEL_WORD[own] || own)}<span class="v5-dim"> · knock-on of ${esc(causeName)}</span></span>`;
        title = `${LEVEL_WORD[own] || own}: ${node.message || ''}\nexplained by ${causeName} (counted once, as that cause)`;
      } else {
        html += `${shape(own)}<span class="v5-msg v5-t-${own}">${esc(node.message || LEVEL_WORD[own] || own)}</span>`;
        title = `${LEVEL_WORD[own] || own}: ${node.message || ''}`;
      }
    }
    const hint = rollupHint(node, st);
    if (hint) {
      html += `<span class="v5-hint" title="${esc(hint.title)}">${shape(hint.level, 'v5-small')}<span>${esc(hint.text)}</span></span>`;
    }
    return `<div class="v5-c v5-c-status" ${title ? `title="${esc(title)}"` : ''}>${html}</div>`;
  }

  function rowHtml(row, st, selected) {
    const node = row.node;
    const detail = (DETAIL.get(node.path) || {}).data || null;
    const own = ownLevel(node);
    // Rule 5: "not connected" (an empty port, an offline goodbye) greys the WHOLE row; an empty bus or group,
    // where nothing is plugged in at all, is drawn the same way.
    const empty = ['bus', 'group', 'port'].includes(node.kind) && node.children.length === 0 && !isProblem(own);
    const off = node.connected === false || empty;
    const sel = selected === node.path;
    const portSel = row.port && selected === row.port.path;
    const hasKids = node.children.length > 0;
    const cls = ['v5-row', 'v5-grid', off ? 'off' : '', sel || portSel ? 'sel' : '', portSel ? 'sel-port' : '',
                 isProblem(own) && !node.knock_on_of ? `p-${own}` : ''].filter(Boolean).join(' ');
    return `<div class="${cls}" role="treeitem" aria-level="${row.depth + 1}" aria-selected="${sel || portSel}"`
      + `${hasKids ? ` aria-expanded="${isOpen(st, node)}"` : ''} data-v5-path="${esc(node.path)}">`
      + nameCell(row, st) + portCell(row)
      + `<div class="v5-c v5-c-id" title="${esc(node.hardware_id || node.id_text || '')}"><span class="v5-val">${esc(node.id_text || '')}</span></div>`
      + rateCell(detail) + seenCell(node) + firmwareCell(node, detail) + statusCell(node, st)
      + '</div>';
  }

  function headHtml(tree) {
    // the STATUS header doubles as the quiet count of independent causes (knock-ons excluded), by shape
    const counts = {};
    (function visit(node) {
      const own = ownLevel(node);
      if (isProblem(own) && !node.knock_on_of) counts[own] = (counts[own] || 0) + 1;
      (node.children || []).forEach(visit);
    })(tree);
    const tally = ['error', 'stale', 'unknown', 'warn'].filter((l) => counts[l])
      .map((l) => `<span class="v5-tally" title="${counts[l]} ${LEVEL_WORD[l]}${counts[l] > 1 ? 's' : ''}">${shape(l, 'v5-small')}${counts[l]}</span>`).join('');
    return '<div class="v5-head v5-grid" role="presentation">'
      + '<div class="v5-c v5-c-name">Name</div><div class="v5-c v5-c-port" title="USB port the device is plugged into">Port</div>'
      + '<div class="v5-c v5-c-id">ID</div><div class="v5-c v5-c-rate" title="measured / expected frequency">Rate</div>'
      + '<div class="v5-c v5-c-seen" title="age of the last periodic report / its expected period">Last seen</div>'
      + '<div class="v5-c v5-c-fw">Firmware</div>'
      + `<div class="v5-c v5-c-status"><span>Status</span><span class="v5-tallies">${tally || '<span class="v5-dim">nothing needs you</span>'}</span></div></div>`;
  }

  // ---------------------------------------------------------------------------------- paint ----
  function selectedPath(st) {
    return st.opts.selectedPath !== undefined && st.opts.selectedPath !== null ? st.opts.selectedPath : st.localSel;
  }

  function paint(container, st) {
    const selected = selectedPath(st);
    const rows = visibleRows(st, st.tree);
    st.rows = rows;
    const keys = rows.map((row) => `${row.port ? `${row.port.path}>` : ''}${row.node.path}`);
    const htmls = rows.map((row) => rowHtml(row, st, selected));
    const head = headHtml(st.tree);

    if (!st.root || st.root.parentNode !== container) {
      container.innerHTML = '<div class="v5" role="tree" tabindex="0" aria-label="IO entities"><div class="v5-head"></div><div class="v5-body" role="group"></div></div>';
      st.root = container.firstElementChild;
      st.body = st.root.lastElementChild;
      st.keys = [];
      st.htmls = [];
      st.headHtml = '';
      bind(container, st);
    }
    if (head !== st.headHtml) {
      st.root.firstElementChild.outerHTML = head;
      st.headHtml = head;
    }
    // Patch rows in place when the row set is unchanged (the usual 1 s tick): only the cells whose text moved
    // are rebuilt, so hover, tooltips and scroll stay put.
    const sameShape = keys.length === st.keys.length && keys.every((key, i) => key === st.keys[i]);
    if (sameShape) {
      for (let i = 0; i < htmls.length; i += 1) {
        if (htmls[i] !== st.htmls[i]) st.body.children[i].outerHTML = htmls[i];
      }
    } else {
      st.body.innerHTML = htmls.join('');
    }
    st.keys = keys;
    st.htmls = htmls;

    rows.forEach((row) => { if (wantsDetail(row.node)) requestDetail(row.node.path, container); });

    if (selected !== st.lastSel) {
      st.lastSel = selected;
      const el = st.body.querySelector('.v5-row.sel');
      if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    }
  }

  // --------------------------------------------------------------------------------- events ----
  function select(container, st, path) {
    st.localSel = path;  // standalone use (no onSelect): the renderer keeps the selection itself
    // optimistic: highlight now; the page confirms by passing selectedPath on its next render
    if (st.opts.selectedPath !== undefined && st.opts.selectedPath !== null) st.opts = { ...st.opts, selectedPath: path };
    if (typeof st.opts.onSelect === 'function') st.opts.onSelect(path);
    paint(container, st);
  }

  function toggle(container, st, path, open) {
    const node = st.byPath.get(path);
    if (!node || !node.children.length) return;
    st.open.set(path, open === undefined ? !isOpen(st, node) : open);
    paint(container, st);
  }

  function bind(container, st) {
    const root = st.root;
    // Our own data-v5-* attributes (not data-path / data-toggle) so a page's document-level handlers never double-act.
    root.addEventListener('click', (event) => {
      const twisty = event.target.closest('[data-v5-toggle]');
      if (twisty) { toggle(container, st, twisty.dataset.v5Toggle); return; }
      const port = event.target.closest('[data-v5-select]');
      if (port) { select(container, st, port.dataset.v5Select); return; }
      const row = event.target.closest('[data-v5-path]');
      if (row) select(container, st, row.dataset.v5Path);
    });
    root.addEventListener('dblclick', (event) => {
      const row = event.target.closest('[data-v5-path]');
      if (row && !event.target.closest('[data-v5-toggle]')) toggle(container, st, row.dataset.v5Path);
    });
    // IDE keys: ↑/↓ move, → opens or steps in, ← closes or steps out, Enter/Space toggles, Home/End jump.
    root.addEventListener('keydown', (event) => {
      const rows = st.rows;
      if (!rows.length) return;
      const selected = selectedPath(st);
      let i = rows.findIndex((row) => row.node.path === selected || (row.port && row.port.path === selected));
      if (i < 0 && selected) {
        // the selection sits inside a closed branch: start from its nearest visible ancestor
        for (let j = rows.length - 1; j >= 0; j -= 1) if (selected.startsWith(`${rows[j].node.path}/`)) { i = j; break; }
      }
      const row = rows[i];
      const go = (j) => { const target = rows[Math.max(0, Math.min(rows.length - 1, j))]; if (target) select(container, st, target.node.path); };
      switch (event.key) {
        case 'ArrowDown': go(i < 0 ? 0 : i + 1); break;
        case 'ArrowUp': go(i < 0 ? 0 : i - 1); break;
        case 'Home': go(0); break;
        case 'End': go(rows.length - 1); break;
        case 'ArrowRight':
          if (!row) { go(0); break; }
          if (row.node.children.length && !isOpen(st, row.node)) toggle(container, st, row.node.path, true);
          else if (row.node.children.length) go(i + 1);
          break;
        case 'ArrowLeft':
          if (!row) { go(0); break; }
          if (row.node.children.length && isOpen(st, row.node)) { toggle(container, st, row.node.path, false); break; }
          for (let j = i - 1; j >= 0; j -= 1) if (rows[j].depth < row.depth) { go(j); break; }
          break;
        case 'Enter': case ' ':
          if (row) toggle(container, st, row.node.path);
          break;
        default: return;
      }
      event.preventDefault();
    });
  }

  // ---------------------------------------------------------------------------------- entry ----
  function render(container, apiTree, opts = {}) {
    if (!container) return;
    const st = stateFor(container);
    if (!apiTree) {
      container.innerHTML = '<div class="v5 v5-empty">No tree yet.</div>';
      st.root = null;
      return;
    }
    st.tree = apiTree;
    st.opts = opts || {};
    st.byPath = indexTree(apiTree);
    const selected = selectedPath(st);
    if (selected && selected !== st.lastSel) revealSelection(st, selected);
    paint(container, st);
  }

  window.IOTreeRenderers = window.IOTreeRenderers || {};
  window.IOTreeRenderers.v5 = { name: 'Columns tree', render };
})();
