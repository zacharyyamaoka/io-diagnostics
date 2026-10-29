// The 3D view (docs/robot-description.md): the robot description's model in the area where the entity page sits, the entity tree
// kept on the left. Zach, 2026-10-29: "basically like Fusion 360 ... the goal is to navigate devices visually".
//
// This file is the shell: it reads GET /api/v1/robot-description, loads the scene module (threed/scene.js, three.js through the
// importmap) the first time the view opens, gives every mapped part its device's own level (the entity's row in /api/v1/live, else
// the page's /state; never a roll-up: one bad CAN board colours that servo's links and nothing above it), keeps the selection in step
// with the tree, and draws ONE of five info layers over the shared scene (the temporary bottom-right "3D info" picker, remembered):
//   v1 Hover card · v2 Callout labels · v3 Peek drawer · v4 Split view · v5 Problems tray.
// The view is a page of the browser's history like the others (app.js NAV_KEYS): view=threed, the selection, and cam (the camera
// pose, replaced as it moves), so Open from 3D, then Back, comes back to the same selection and camera.
// Without a description the item still shows, with a calm empty state saying how to add one; nothing else on the page changes.
'use strict';
(function () {
  const VARIANT_KEY = 'io-diagnostics.threed.variant';
  const NS = 'io-diagnostics.threed.';
  const VARIANTS = [
    { id: 'v1', name: 'Hover card', description: 'A card at the cursor: name, level, the worst checks and key State values, Open. Click pins it.' },
    { id: 'v2', name: 'Callout labels', description: 'Leader lines from each device that is not OK to its name and status (all of them on a toggle).' },
    { id: 'v3', name: 'Peek drawer', description: 'Selecting a part opens a narrow right drawer: the entity summary and its Diagnostics rows, Open.' },
    { id: 'v4', name: 'Split view', description: 'The viewport on top, the selected entity\'s own device page (its tabs) below.' },
    { id: 'v5', name: 'Problems tray', description: 'A bottom strip of every device that is not OK: click flies to it, double-click opens it.' },
  ];
  const RANK = { ok: 0, warn: 1, unknown: 2, stale: 3, error: 4 };
  const PROBLEM = new Set(['warn', 'unknown', 'stale', 'error']);
  const DESCRIPTION_EVERY_MS = 5000;
  // WHY a fixed blue: Fusion's selection is blue in every theme; the skins' own selection tokens are neutral greys (a row's selection
  // must carry no colour), which would read as "unmapped" on a grey body
  const SELECT = '#2f7cf6';
  const EXAMPLE = `version = 1
urdf = "/home/you/robot/urdf/robot.urdf"   # meshes resolve next to it

[[part]]                                   # one per device you can place
link = "one_dof_arm"                       # or links = ["a", "b"]
hardware_id = "usb:0483:5740:0607623A"     # the adapter, as in expected.toml
node = "can/12"                            # the board below it

[[unmapped_device]]                        # optional: what you could not place
name = "RealSense D405 218622274411"
serial = "218622274411"
reason = "no mount for it in the model"`;

  const T = {
    api: null, mounted: false, shown: false, el: {}, desc: null, descAt: 0, descLoading: false, loadKey: '', scene: null, sceneError: '',
    loading: false, variant: 'v1', live: new Map(), source: null, hover: { part: -1, x: 0, y: 0 }, pinned: -1, appliedCam: null,
    lastSel: undefined, fromScene: false, labelsAll: false, inspectorMark: null, hideTimer: 0, tipPart: -1, colorsKey: '',
  };
  const read = (key) => { try { return localStorage.getItem(key); } catch (_) { return null; } };
  T.variant = VARIANTS.some((v) => v.id === read(VARIANT_KEY)) ? read(VARIANT_KEY) : 'v1';
  // Back / Forward restore a selection with its own camera: a selection that arrives that way is never re-framed
  window.addEventListener('popstate', () => { T.fromNav = true; });
  const write = (key, value) => { try { localStorage.setItem(key, value); } catch (_) { /* private mode: lasts this page only */ } };
  const $ = (selector, root = T.el.root) => (root ? root.querySelector(selector) : null);
  const esc = (text) => (T.api ? T.api.esc(text) : String(text ?? ''));
  const S = () => T.api.S;

  // ------------------------------------------------------------------------------------------- the data ----
  function wantDescription(force) {
    if (T.descLoading || (!force && T.desc && performance.now() - T.descAt < DESCRIPTION_EVERY_MS)) return;
    T.descLoading = true;
    fetch('/api/v1/robot-description', { cache: 'no-store' })
      .then((response) => response.json())
      .then((answer) => { T.desc = answer; })
      .catch((error) => { T.desc = { present: false, problem: `the host did not answer: ${error}`, parts: [], unmapped_devices: [] }; })
      .finally(() => { T.descLoading = false; T.descAt = performance.now(); if (T.shown) T.api.render(); });
  }
  const usable = () => Boolean(T.desc && T.desc.present && !T.desc.problem && T.desc.urdf);
  const parts = () => (T.desc && T.desc.parts) || [];

  // The live feed (GET /api/v1/live, change-only): each tree row as the host last sent it. WHY its own copy and not only the page's
  // 1 s /state: a board's level reaches its part as soon as the host has it; the page's rows stay the fallback.
  function openLive() {
    if (T.source || !window.EventSource) return;
    const source = new EventSource('/api/v1/live');
    T.source = source;
    const keep = (node) => { T.live.set(node.path, node); (node.children || []).forEach((child) => { if (child && typeof child === 'object') keep(child); }); };
    source.addEventListener('snapshot', (event) => {
      try { const body = JSON.parse(event.data); T.live.clear(); if (body.tree) keep(body.tree); } catch (_) { return; }
      paint();
    });
    source.addEventListener('upsert', (event) => {
      try { const body = JSON.parse(event.data); if (body.table === 'tree' && body.row) T.live.set(body.id, body.row); } catch (_) { return; }
      paint();
    });
    source.addEventListener('remove', (event) => {
      try { const body = JSON.parse(event.data); if (body.table === 'tree') T.live.delete(body.id); } catch (_) { return; }
      paint();
    });
  }
  function closeLive() { if (T.source) { T.source.close(); T.source = null; } }
  const rowOf = (path) => (path ? T.live.get(path) || (S().byPath && S().byPath.get(path)) || null : null);

  // every part's state: its device's own level (no roll-up), a ghost when it is not detected (no row, or the row is not connected)
  function partState(part) {
    const row = rowOf(part.path);
    const ghost = !row || row.connected === false;
    return { row, ghost, level: ghost ? null : row.level || null };
  }
  function selectedParts() {
    const sel = S().selKey ? null : S().sel;
    const out = new Set();
    if (!sel) return out;
    parts().forEach((part, index) => { if (part.path && (part.path === sel || part.path.startsWith(`${sel}/`))) out.add(index); });
    return out;
  }
  const nameOf = (part) => (part.path ? T.api.entityLabel(part.path) : '') || part.name || part.links[0];
  const problemOf = (state) => (state.ghost ? 'absent' : PROBLEM.has(state.level) ? state.level : null);
  const levelWord = (state) => (state.ghost ? 'not detected' : state.level || 'no reports');

  // ------------------------------------------------------------------------------------------- colours ----
  // the app's level tokens for the current skin and mode, as plain sRGB the scene can use (a canvas resolves color-mix, system colours)
  function colors() {
    const probe = T.el.probe;
    const canvas = T.el.swatch || (T.el.swatch = document.createElement('canvas'));
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const resolve = (value) => {
      probe.style.color = '';
      probe.style.color = value;
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = '#000';
      ctx.fillStyle = getComputedStyle(probe).color;
      ctx.fillRect(0, 0, 1, 1);
      const [r, g, b] = ctx.getImageData(0, 0, 1, 1).data;
      return `#${[r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')}`;
    };
    const dark = document.documentElement.dataset.mode === 'dark';
    return {
      bg: resolve('var(--io-surface-alt)'), grid: resolve('var(--io-border)'), grid2: resolve('var(--io-border-soft)'),
      neutral: dark ? '#8d929b' : '#c2c5cb', select: SELECT,
      ok: resolve('var(--io-ok)'), warn: resolve('var(--io-warn)'), error: resolve('var(--io-error)'),
      unknown: resolve('var(--io-unknown)'), stale: resolve('var(--io-stale)'),
    };
  }
  function applyColors() {
    if (!T.scene) return;
    const key = `${document.documentElement.dataset.skin}|${document.documentElement.dataset.mode}`;
    if (key === T.colorsKey) return;
    T.colorsKey = key;
    T.scene.setColors(colors());
  }

  // ----------------------------------------------------------------------------------------- the DOM ----
  function sashOf(name, o) {
    // a boundary of this view: draggable, remembered, double-click for the default, arrow keys; the panel beside it folds
    const el = T.el.root.querySelector(`[data-td-sash="${name}"]`);
    const stored = Number(read(`${NS}${name}`));
    const st = { size: stored > 0 ? stored : o.def, folded: read(`${NS}${name}.fold`) === '1' };
    const clamp = (value) => Math.round(Math.max(o.min, Math.min(value, o.max())));
    const apply = () => { T.el.root.style.setProperty(o.varName, `${clamp(st.size)}px`); T.el.root.classList.toggle(`td-fold-${name}`, st.folded); };
    let drag = null;
    el.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      drag = { id: event.pointerId };
      el.setPointerCapture(event.pointerId);
      el.classList.add('on');
      document.body.classList.add('td-dragging', o.axis === 'x' ? 'td-x' : 'td-y');
      event.preventDefault();
    });
    el.addEventListener('pointermove', (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      const rect = T.el.root.getBoundingClientRect();
      st.size = clamp(o.axis === 'x' ? rect.right - event.clientX : rect.bottom - event.clientY);
      apply();
    });
    const end = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      drag = null;
      el.classList.remove('on');
      document.body.classList.remove('td-dragging', 'td-x', 'td-y');
      write(`${NS}${name}`, String(st.size));
    };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
    el.addEventListener('dblclick', () => { st.size = o.def; write(`${NS}${name}`, ''); apply(); });
    el.addEventListener('keydown', (event) => {
      const keys = o.axis === 'x' ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown'];
      if (!keys.includes(event.key)) return;
      st.size = clamp(st.size + (event.key === keys[0] ? 16 : -16));
      write(`${NS}${name}`, String(st.size));
      apply();
      event.preventDefault();
    });
    window.addEventListener('resize', apply);
    apply();
    return { fold(folded) { st.folded = Boolean(folded); write(`${NS}${name}.fold`, st.folded ? '1' : '0'); apply(); paint(); }, folded: () => st.folded, size: () => st.size, apply };
  }

  function mount() {
    const host = document.getElementById('threed-host');
    if (!host || T.mounted) return;
    T.mounted = true;
    host.innerHTML = `<div class="td-root" data-variant="v1">
      <div class="td-empty" hidden></div>
      <div class="td-top">
        <div class="td-view">
          <div class="td-tools">
            <button type="button" class="td-btn" data-td-home title="Fit the whole model ( F )" aria-label="Fit the whole model"><svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button>
            <button type="button" class="td-btn td-only-v2" data-td-labels aria-pressed="false" title="Label every mapped device, not only the ones that are not OK"><svg viewBox="0 0 24 24"><path d="M4 6h10l4 6-4 6H4z"/></svg><span>All labels</span></button>
          </div>
          <svg class="td-lines" aria-hidden="true"></svg>
          <div class="td-chips"></div>
          <div class="td-card" hidden></div>
          <div class="td-tip" role="tooltip" hidden></div>
          <div class="td-legend"></div>
          <div class="td-status" aria-live="polite"></div>
        </div>
        <div class="td-sash td-sash-x" data-td-sash="drawer" role="separator" aria-orientation="vertical" aria-label="Resize the drawer" tabindex="0" title="Drag to resize · double-click to reset"></div>
        <aside class="td-drawer" aria-label="Selected device"></aside>
        <button type="button" class="td-reopen" data-td-reopen="drawer" title="Show the drawer" aria-label="Show the drawer"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg></button>
      </div>
      <div class="td-sash td-sash-y" data-td-sash="split" role="separator" aria-orientation="horizontal" aria-label="Resize the device page" tabindex="0" title="Drag to resize · double-click to reset"></div>
      <div class="td-sash td-sash-y" data-td-sash="tray" role="separator" aria-orientation="horizontal" aria-label="Resize the problems tray" tabindex="0" title="Drag to resize · double-click to reset"></div>
      <div class="td-bottom">
        <div class="td-bar"><span class="td-bar-t"></span><span class="td-fill"></span>
          <button type="button" class="td-btn" data-td-fold="bottom" title="Fold the panel away" aria-label="Fold the panel away"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button></div>
        <div class="td-bottom-body"><div class="td-tray" role="list"></div><div class="td-split-slot"></div></div>
      </div>
      <button type="button" class="td-reopen td-reopen-y" data-td-reopen="bottom" title="Show the panel" aria-label="Show the panel"><svg viewBox="0 0 24 24"><path d="m6 15 6-6 6 6"/></svg><span></span></button>
      <span class="td-probe" aria-hidden="true"></span>
    </div>`;
    const root = host.firstElementChild;
    T.el = { host, root, view: $('.td-view', root), lines: $('.td-lines', root), chips: $('.td-chips', root), card: $('.td-card', root), tip: $('.td-tip', root),
      legend: $('.td-legend', root), status: $('.td-status', root), drawer: $('.td-drawer', root), tray: $('.td-tray', root), slot: $('.td-split-slot', root),
      empty: $('.td-empty', root), barTitle: $('.td-bar-t', root), probe: $('.td-probe', root) };
    T.drawerSash = sashOf('drawer', { axis: 'x', def: 320, min: 240, max: () => Math.max(260, root.getBoundingClientRect().width - 360), varName: '--td-drawer-w' });
    // the split (v4) and the tray (v5) share the bottom panel, each with its own remembered height
    T.splitSash = sashOf('split', { axis: 'y', def: 360, min: 140, max: () => Math.max(180, root.getBoundingClientRect().height - 160), varName: '--td-split-h' });
    T.traySash = sashOf('tray', { axis: 'y', def: 150, min: 70, max: () => Math.max(120, root.getBoundingClientRect().height - 160), varName: '--td-tray-h' });
    root.addEventListener('click', onClick);
    root.addEventListener('dblclick', onDouble);
    T.el.card.addEventListener('pointerenter', () => clearTimeout(T.hideTimer));
    T.el.card.addEventListener('pointerleave', () => { if (T.pinned < 0) hideCardSoon(); });
    document.addEventListener('keydown', (event) => {
      if (!T.shown || (event.target.closest && event.target.closest('input, select, textarea, [contenteditable], .xterm'))) return;
      if (event.key === 'Escape' && T.pinned >= 0) { T.pinned = -1; paint(); }
      if ((event.key === 'f' || event.key === 'F') && !event.ctrlKey && !event.metaKey && !event.altKey && T.scene) T.scene.home();
    });
    new MutationObserver(() => { T.colorsKey = ''; applyColors(); paint(); }).observe(document.documentElement, { attributes: true, attributeFilter: ['data-mode', 'data-skin'] });
    setupPicker();
  }

  // the TEMPORARY "3D info" picker in the bottom-right cluster (Zach's rule: a variant gated for review is a drop-down, never a URL flag)
  function setupPicker() {
    const controls = document.getElementById('controls');
    if (!controls) return;
    const box = document.createElement('div');
    box.className = 'ctl';
    box.id = 'threed-switch';
    box.title = '3D info: how the 3D view shows a device (temporary: five proposals)';
    box.hidden = true;
    box.innerHTML = `<span class="ctl-label">3D info</span><select id="threed-variant" aria-label="3D info">${VARIANTS.map((v) => `<option value="${v.id}" title="${esc(v.description)}">${v.id} · ${esc(v.name)}</option>`).join('')}</select>`;
    controls.appendChild(box);
    const select = box.querySelector('select');
    select.value = T.variant;
    select.addEventListener('change', () => setVariant(select.value));
    T.el.picker = box;
  }
  function setVariant(id) {
    if (!VARIANTS.some((v) => v.id === id)) return;
    T.variant = id;
    write(VARIANT_KEY, id);
    if (T.el.picker) T.el.picker.querySelector('select').value = id;
    T.pinned = -1;
    T.api.render();
  }

  // ------------------------------------------------------------------------------------------- the scene ----
  async function ensureScene() {
    if (!usable()) return;
    const key = `${T.desc.urdf}|${parts().map((part) => part.links.join('+')).join(',')}`;
    if (T.loadKey === key || T.loading) return;
    T.loading = true;
    T.sceneError = '';
    if (T.scene) { T.scene.dispose(); T.scene = null; }
    T.loadKey = key;
    try {
      const module = await import('./scene.js');
      T.colorsKey = '';
      T.scene = module.createScene(T.el.view, {
        onHover, onPick, onDouble: onSceneDouble,
        onCamera(text) { T.appliedCam = text; if (S().view === 'threed') { S().cam = text; T.api.replaceNav(); } },
        onFrame: drawLayer,
      });
      applyColors();
      paint();
      const loading = T.scene.load(T.desc.urdf, parts());
      status();
      const tick = setInterval(status, 200);
      await loading;
      clearInterval(tick);
      if (S().cam) { T.appliedCam = S().cam; T.scene.setCamera(S().cam); } else T.api.replaceNav();
    } catch (error) {
      console.error('3D view:', error);
      T.sceneError = String(error && error.message ? error.message : error);
    }
    T.loading = false;
    T.lastSel = undefined;
    status();
    T.api.render();
  }
  function status() {
    const el = T.el.status;
    if (!el) return;
    const timings = T.scene ? T.scene.timings() : null;
    let text = '';
    if (T.sceneError) text = `The model did not load: ${T.sceneError}`;
    else if (timings && !timings.done) text = `Loading the model · ${timings.meshes} of ${timings.total || '…'} meshes`;
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle('err', Boolean(T.sceneError));
  }

  // ------------------------------------------------------------------------------------------- painting ----
  function paint() {
    if (!T.shown || !T.mounted) return;
    const list = parts();
    const selected = selectedParts();
    const states = list.map((part, index) => ({ ...partState(part), selected: selected.has(index) }));
    T.states = states;
    if (T.scene) T.scene.setStates(states.map((state) => ({ level: state.level, ghost: state.ghost, selected: state.selected })));
    drawLegend(states);
    drawDrawer();
    drawTray(states);
    drawCard();
    drawLayer();
  }

  function shapeOf(state) {
    if (state.ghost) return '<svg class="td-ghost-mark" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.4 2"/></svg>';
    return T.api.shape(state.level, state.level || '');
  }
  function drawLegend(states) {
    const count = (fn) => states.filter(fn).length;
    const items = [
      ['error', count((s) => !s.ghost && s.level === 'error')], ['warn', count((s) => !s.ghost && s.level === 'warn')],
      ['unknown', count((s) => !s.ghost && s.level === 'unknown')], ['stale', count((s) => !s.ghost && s.level === 'stale')],
      ['ok', count((s) => !s.ghost && s.level === 'ok')], ['absent', count((s) => s.ghost)],
    ];
    const words = { error: 'error', warn: 'warn', unknown: 'unknown', stale: 'stale', ok: 'OK', absent: 'not detected' };
    const unmapped = (T.desc && T.desc.unmapped_devices) || [];
    const timings = T.scene && T.scene.loaded() ? T.scene.timings() : null;
    const html = `<div class="td-leg-rows">${items.map(([level, n]) => `<span class="td-leg td-leg-${level}${n ? '' : ' none'}" title="${n} part${n === 1 ? '' : 's'}: ${words[level]}"><i></i>${words[level]}${n ? ` <b>${n}</b>` : ''}</span>`).join('')}`
      + '<span class="td-leg td-leg-plain" title="Grey: a part no device is mapped to"><i></i>not mapped</span></div>'
      + (unmapped.length ? `<details class="td-unmapped"><summary>${unmapped.length} device${unmapped.length === 1 ? '' : 's'} not in the model</summary>`
        + `<ul>${unmapped.map((item) => `<li title="${esc(item.reason)}"><span>${esc(item.name)}</span><small>${esc(item.reason)}</small></li>`).join('')}</ul></details>` : '')
      + (timings ? `<div class="td-load" title="Time to the first drawn frame with geometry, and to every mesh loaded">${timings.meshes} meshes · first frame ${(timings.firstFrame / 1000).toFixed(2)} s · all ${(timings.done / 1000).toFixed(2)} s</div>` : '');
    if (T.el.legend.dataset.html !== html) { T.el.legend.dataset.html = html; T.el.legend.innerHTML = html; }
  }

  // the info a card or a drawer shows for an entity: its row, its worst checks (/api/v1/checks) and its State (/api/v1/values)
  function checksOf(path) {
    const entry = T.api.cached('/api/v1/checks', 2000);
    return ((entry.data && entry.data.checks && entry.data.checks[path]) || []).slice()
      .sort((a, b) => (RANK[b.effective_level] ?? -1) - (RANK[a.effective_level] ?? -1));
  }
  function valuesOf(path) {
    const entry = T.api.cached(`/api/v1/values?path=${encodeURIComponent(path)}`, 2000);
    const rows = [];
    for (const group of (entry.data && entry.data.groups) || []) for (const row of group.rows || []) rows.push(row);
    return rows;
  }
  const valueText = (row) => {
    const value = typeof row.value === 'number' ? (Math.abs(row.value) >= 100 ? row.value.toFixed(0) : +row.value.toFixed(3)) : row.value;
    return `${value ?? '—'}${row.unit ? ` ${row.unit === 'Cel' ? '°C' : row.unit}` : ''}${row.meaning ? ` · ${row.meaning}` : ''}`;
  };
  function checkRows(checks, limit) {
    return checks.slice(0, limit).map((check) => `<div class="td-check"><span class="td-check-s">${T.api.shape(check.effective_level, check.effective_level)}</span><span class="td-check-n">${esc(check.name)}</span>`
      + `<span class="td-check-m" title="${esc(check.message)}">${esc(T.api.usb(check.message || ''))}</span></div>`).join('');
  }
  function headHtml(part, state) {
    const row = state.row;
    return `<div class="td-head"><span class="td-name">${esc(nameOf(part))}</span>${state.ghost ? '<span class="pill td-absent">not detected</span>' : T.api.pill(state.level)}</div>`
      + `<div class="td-msg">${esc(state.ghost ? (part.path ? 'Its row is not connected.' : 'No device here matches this part.') : T.api.usb((row && row.message) || ''))}</div>`;
  }
  function openButton(part) {
    return part.path && rowOf(part.path) ? `<button type="button" class="td-open" data-td-open="${esc(part.path)}">Open<svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg></button>` : '';
  }

  // v1's rich card, the small card of v2-v4, v5's tooltip
  function drawCard() {
    const card = T.el.card;
    const tip = T.el.tip;
    const list = parts();
    const index = T.pinned >= 0 ? T.pinned : T.hover.part;
    const part = list[index];
    if (T.variant === 'v5') {
      card.hidden = true;
      if (!part) { tip.hidden = true; return; }
      const state = (T.states || [])[index] || partState(part);
      tip.textContent = `${nameOf(part)} · ${levelWord(state)}`;
      tip.hidden = false;
      place(tip, T.hover.x, T.hover.y);
      return;
    }
    tip.hidden = true;
    if (!part) { card.hidden = true; return; }
    const state = (T.states || [])[index] || partState(part);
    let html = headHtml(part, state);
    if (T.variant === 'v1' && part.path) {
      const checks = checksOf(part.path);
      if (checks.length) html += `<div class="td-sec">${checkRows(checks, 3)}</div>`;
      const values = valuesOf(part.path).slice(0, 3);
      if (values.length) html += `<div class="td-sec td-kv">${values.map((row) => `<span class="k">${esc(row.key)}</span><span class="v">${esc(valueText(row))}</span>`).join('')}</div>`;
    }
    html += `<div class="td-foot"><span class="td-path" title="${esc(part.path || '')}">${esc(part.links.join(', '))}</span>${openButton(part)}</div>`;
    if (T.variant === 'v1') html += `<div class="td-pin">${T.pinned >= 0 ? 'Pinned · Esc or a click elsewhere unpins' : 'Click to pin'}</div>`;
    if (card.dataset.html !== html) { card.dataset.html = html; card.innerHTML = html; }
    card.classList.toggle('rich', T.variant === 'v1');
    card.classList.toggle('pinned', T.pinned >= 0);
    card.dataset.part = String(index);
    card.hidden = false;
    if (T.pinned < 0 || !card.dataset.placed) { place(card, T.hover.x, T.hover.y); card.dataset.placed = T.pinned >= 0 ? '1' : ''; }
  }
  function place(el, clientX, clientY) {
    const view = T.el.view.getBoundingClientRect();
    const width = el.offsetWidth || 240;
    const height = el.offsetHeight || 80;
    let x = clientX - view.left + 14;
    let y = clientY - view.top + 14;
    if (x + width > view.width - 8) x = Math.max(8, clientX - view.left - width - 14);
    if (y + height > view.height - 8) y = Math.max(8, view.height - height - 8);
    el.style.left = `${Math.round(x)}px`;
    el.style.top = `${Math.round(y)}px`;
  }
  function hideCardSoon() {
    clearTimeout(T.hideTimer);
    T.hideTimer = setTimeout(() => { if (T.pinned < 0 && T.hover.part < 0) { T.el.card.hidden = true; } }, 260);
  }

  // v2: callout labels, re-placed on every drawn frame (they follow the camera)
  function drawLayer() {
    if (!T.mounted) return;
    const lines = T.el.lines;
    const chips = T.el.chips;
    if (T.variant !== 'v2' || !T.scene || !T.states) { if (lines.innerHTML) lines.innerHTML = ''; if (chips.innerHTML) chips.innerHTML = ''; return; }
    const view = T.el.view.getBoundingClientRect();
    const wanted = [];
    parts().forEach((part, index) => {
      const state = T.states[index];
      if (!T.labelsAll && !problemOf(state)) return;
      const at = T.scene.project(index);
      if (!at || !at.inside) return;
      wanted.push({ index, part, state, x: at.x - view.left, y: at.y - view.top });
    });
    // labels sit to the right of their part, stacked so they never overlap (Fusion's leaders); the worst first
    wanted.sort((a, b) => a.y - b.y);
    const placed = [];
    const chipH = 26;
    for (const item of wanted) {
      const right = item.x < view.width - 220;
      let y = Math.max(8, item.y - 46);
      for (const other of placed) if (other.right === right && Math.abs(other.cy - y) < chipH + 4) y = other.cy + chipH + 4;
      item.cy = Math.min(y, view.height - chipH - 8);
      item.cx = right ? Math.min(item.x + 54, view.width - 210) : Math.max(8, item.x - 54 - 200);
      item.right = right;
      placed.push(item);
    }
    let svg = '';
    let html = '';
    for (const item of placed) {
      const level = problemOf(item.state) || item.state.level || 'none';
      const endX = item.right ? item.cx : item.cx + 200;
      svg += `<line x1="${item.x.toFixed(1)}" y1="${item.y.toFixed(1)}" x2="${endX.toFixed(1)}" y2="${(item.cy + chipH / 2).toFixed(1)}" class="lv-${level}"/>`
        + `<circle cx="${item.x.toFixed(1)}" cy="${item.y.toFixed(1)}" r="3" class="lv-${level}"/>`;
      html += `<button type="button" class="td-chip lv-${level}${item.state.selected ? ' sel' : ''}" data-td-part="${item.index}" style="left:${item.cx.toFixed(0)}px;top:${item.cy.toFixed(0)}px"`
        + ` title="${esc(nameOf(item.part))}: ${esc(levelWord(item.state))}">${shapeOf(item.state)}<span>${esc(nameOf(item.part))}</span><small>${esc(levelWord(item.state))}</small></button>`;
    }
    lines.setAttribute('viewBox', `0 0 ${Math.round(view.width)} ${Math.round(view.height)}`);
    if (lines.dataset.svg !== svg) { lines.dataset.svg = svg; lines.innerHTML = svg; }
    if (chips.dataset.html !== html) { chips.dataset.html = html; chips.innerHTML = html; }
  }

  // v3: the peek drawer, the selection's summary and its Diagnostics rows (/api/v1/node, the page's own copy)
  function drawDrawer() {
    if (T.variant !== 'v3') return;
    const s = S();
    const node = s.node && s.node.node && s.node.node.path === s.sel ? s.node : null;
    const index = parts().findIndex((part) => part.path && part.path === s.sel);
    let html = '<div class="td-dr-bar"><span class="td-bar-t">Selected</span><span class="td-fill"></span>'
      + '<button type="button" class="td-btn" data-td-fold="drawer" title="Fold the drawer away" aria-label="Fold the drawer away"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg></button></div>';
    if (!s.sel || s.selKey) html += '<div class="td-dr-empty">Click a part, or a row in the tree, to see its device here.</div>';
    else if (!node) html += '<div class="td-dr-empty">Loading…</div>';
    else {
      const row = node.node;
      const mapped = index >= 0;
      html += `<div class="td-dr-body"><div class="td-head">${T.api.icon(row.icon)}<span class="td-name">${esc(T.api.entityLabel(row.path) || row.name)}</span>${T.api.pill(row.level)}</div>`
        + `<div class="td-msg">${esc(T.api.usb(row.message || ''))}</div>`
        + `<div class="td-path" title="${esc(row.path)}">${esc(row.path)}</div>`
        + (mapped ? `<div class="td-links">Links: ${esc(parts()[index].links.join(', '))}</div>` : '<div class="td-links muted">Not mapped to a part of the model.</div>')
        + `<div class="td-sec-h">Diagnostics <span>${node.checks.length}</span></div>`
        + (node.checks.length ? checkRows(node.checks.slice().sort((a, b) => (RANK[b.effective_level] ?? -1) - (RANK[a.effective_level] ?? -1)), 40) : '<div class="muted">No checks of its own.</div>')
        + `<div class="td-dr-foot"><button type="button" class="td-open" data-td-open="${esc(row.path)}">Open the device page<svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg></button></div></div>`;
    }
    if (T.el.drawer.dataset.html !== html) { T.el.drawer.dataset.html = html; T.el.drawer.innerHTML = html; }
  }

  // v5: the problems tray, every mapped device that is not OK, worst first (a ghost is "not detected")
  function drawTray(states) {
    if (T.variant !== 'v5') return;
    const rank = (state) => (state.ghost ? 2.5 : RANK[state.level] ?? -1);
    const items = parts().map((part, index) => ({ part, index, state: states[index] })).filter((item) => problemOf(item.state))
      .sort((a, b) => rank(b.state) - rank(a.state));
    T.el.barTitle.textContent = `Problems · ${items.length}`;
    const html = items.length ? items.map(({ part, index, state }) => `<div class="td-item${state.selected ? ' sel' : ''}" role="listitem" tabindex="0" data-td-part="${index}"`
      + ` title="${esc(nameOf(part))}: ${esc(levelWord(state))}${state.row && state.row.message ? ` · ${esc(state.row.message)}` : ''} · click to fly to it, double-click to open">`
      + `${shapeOf(state)}<span class="td-item-n">${esc(nameOf(part))}</span><span class="td-item-m">${esc(state.ghost ? 'not detected' : T.api.usb((state.row && state.row.message) || state.level))}</span></div>`).join('')
      : '<div class="td-dr-empty">Every mapped device is OK.</div>';
    if (T.el.tray.dataset.html !== html) { T.el.tray.dataset.html = html; T.el.tray.innerHTML = html; }
    const reopen = T.el.root.querySelector('.td-reopen-y span');
    if (reopen) reopen.textContent = `Problems · ${items.length}`;
  }

  // v4: the selected entity's real device page (the page's own #inspector, moved here and back)
  function placeInspector(inSplit) {
    const inspector = document.getElementById('inspector');
    if (!inspector) return;
    if (inSplit && inspector.parentNode !== T.el.slot) {
      T.inspectorMark = document.createComment('threed:inspector');
      inspector.parentNode.insertBefore(T.inspectorMark, inspector);
      T.el.slot.appendChild(inspector);
    } else if (!inSplit && T.inspectorMark && T.inspectorMark.parentNode) {
      T.inspectorMark.replaceWith(inspector);
      T.inspectorMark = null;
    }
    if (inSplit) T.el.barTitle.textContent = 'Device page';
  }

  // --------------------------------------------------------------------------------------------- events ----
  function selectPart(index, frame) {
    const part = parts()[index];
    if (!part) return;
    if (part.path && rowOf(part.path)) {
      T.fromScene = !frame;
      T.api.select(part.path);
    }
    if (frame && T.scene) T.scene.frameParts([index]);
  }
  function onHover(part, x, y) {
    T.hover = { part, x, y };
    if (part >= 0) clearTimeout(T.hideTimer);
    if (T.pinned >= 0 && T.variant === 'v1') return;
    if (part < 0 && T.variant !== 'v5') { hideCardSoon(); return; }
    drawCard();
  }
  function onPick(part) {
    if (part < 0) { if (T.pinned >= 0) { T.pinned = -1; drawCard(); } return; }
    if (T.variant === 'v1') { T.pinned = part; T.el.card.dataset.placed = ''; }
    selectPart(part, false);
    paint();
  }
  function onSceneDouble(part) {
    const target = parts()[part];
    if (target && target.path && rowOf(target.path)) open(target.path);
  }
  // Open: the entity's own device page, one history step after the 3D view (which keeps its selection and camera for Back)
  function open(path) {
    if (T.scene && S().view === 'threed') { S().cam = T.scene.cameraText(); T.appliedCam = S().cam; T.api.replaceNav(); }
    T.pinned = -1;
    T.api.open(path);
  }
  function onClick(event) {
    const t = event.target;
    const opener = t.closest('[data-td-open]');
    if (opener) { event.stopPropagation(); open(opener.dataset.tdOpen); return; }
    if (t.closest('[data-td-home]')) { if (T.scene) T.scene.home(); return; }
    const labels = t.closest('[data-td-labels]');
    if (labels) { T.labelsAll = !T.labelsAll; labels.setAttribute('aria-pressed', String(T.labelsAll)); labels.classList.toggle('on', T.labelsAll); drawLayer(); return; }
    const fold = t.closest('[data-td-fold]');
    if (fold) { sashFor(fold.dataset.tdFold).fold(true); return; }
    const reopen = t.closest('[data-td-reopen]');
    if (reopen) { sashFor(reopen.dataset.tdReopen).fold(false); return; }
    const item = t.closest('[data-td-part]');
    if (item) { selectPart(Number(item.dataset.tdPart), true); }
  }
  const sashFor = (name) => (name === 'drawer' ? T.drawerSash : T.variant === 'v4' ? T.splitSash : T.traySash);
  function onDouble(event) {
    const item = event.target.closest('[data-td-part]');
    if (!item) return;
    const part = parts()[Number(item.dataset.tdPart)];
    if (part && part.path && rowOf(part.path)) open(part.path);
  }

  // ------------------------------------------------------------------------------------------ the empty state ----
  function drawEmpty() {
    const desc = T.desc;
    const problem = desc && desc.present && desc.problem;
    const html = !desc ? '<div class="td-empty-in"><p class="muted">Reading the robot description…</p></div>'
      : `<div class="td-empty-in">
        <svg class="td-empty-ic" viewBox="0 0 24 24" aria-hidden="true"><path d="m12 2 9 5v10l-9 5-9-5V7z"/><path d="m3 7 9 5 9-5M12 12v10"/></svg>
        <h2>${problem ? 'The robot description does not load' : 'No robot description'}</h2>
        ${problem ? `<p class="td-problem">${esc(desc.problem)}</p>` : '<p>The 3D view draws your robot from its URDF and colours each part by the device it is mapped to. Nothing else needs it: every other view works without one.</p>'}
        <p>${problem ? 'Fix' : 'Add'} <code>${esc((desc && desc.file_name) || 'robot-description.toml')}</code> in the config folder${desc && desc.file ? `:</p><p class="td-where"><code>${esc(desc.file)}</code><button type="button" class="td-btn td-copy" data-td-copy="${esc(desc.file)}" title="Copy the path">Copy</button>` : '.'}</p>
        <p>A TOML file: the URDF's path and one <code>[[part]]</code> per device, matched the way <code>expected.toml</code> matches one.</p>
        <pre class="td-example">${esc(EXAMPLE)}</pre>
        <p class="muted">The whole format: <span class="td-docs">${esc((desc && desc.docs) || 'docs/robot-description.md')}</span>
          <button type="button" class="td-btn td-copy" data-td-copy="${esc((desc && desc.docs) || 'docs/robot-description.md')}" title="Copy the path of the docs">Copy</button></p>
      </div>`;
    if (T.el.empty.dataset.html !== html) { T.el.empty.dataset.html = html; T.el.empty.innerHTML = html; }
  }
  document.addEventListener('click', (event) => {
    const copy = event.target.closest && event.target.closest('[data-td-copy]');
    if (!copy) return;
    const done = () => { copy.textContent = 'Copied'; setTimeout(() => { copy.textContent = 'Copy'; }, 1400); };
    if (navigator.clipboard) navigator.clipboard.writeText(copy.dataset.tdCopy).then(done, () => {});
  });

  // ---------------------------------------------------------------------------------------- the page's seam ----
  // app.js calls update(on) after every render; on = the 3D view is the page's view
  function update(on) {
    if (!T.api) return;
    if (T.el.picker) T.el.picker.hidden = !on;
    if (!on) {
      if (T.shown) {
        T.shown = false;
        closeLive();
        if (T.scene) T.scene.pause(true);
        if (T.mounted) placeInspector(false);
        T.pinned = -1;
      }
      return;
    }
    mount();
    const entering = !T.shown;
    if (entering) { T.shown = true; if (T.scene) T.scene.pause(false); wantDescription(true); } else wantDescription(false);
    const root = T.el.root;
    root.dataset.variant = T.variant;
    const ready = usable();
    T.el.empty.hidden = ready;
    root.classList.toggle('td-none', !ready);
    if (!ready) { drawEmpty(); placeInspector(false); closeLive(); return; }
    openLive();
    placeInspector(T.variant === 'v4');
    if (T.variant === 'v4' && T.el.barTitle) T.el.barTitle.textContent = 'Device page';
    ensureScene();
    applyColors();
    // the camera of the history entry (Back / Forward), then the selection framed when it changed for any other reason
    const s = S();
    const camFromNav = Boolean(s.cam && s.cam !== T.appliedCam && T.scene && T.scene.loaded());
    if (camFromNav) { T.appliedCam = s.cam; T.scene.setCamera(s.cam); }
    const sel = s.selKey ? null : s.sel;
    if (T.scene && T.scene.loaded() && sel !== T.lastSel) {
      const first = T.lastSel === undefined;
      T.lastSel = sel;
      const indexes = [...selectedParts()];
      // framed only when the selection changed while the 3D view was showing, and not by Back / Forward or a click in the scene
      if (!first && !entering && !camFromNav && !T.fromScene && !T.fromNav && indexes.length) T.scene.frameParts(indexes);
      T.fromScene = false;
    }
    T.fromNav = false;
    paint();
  }

  window.IOThreeD = {
    VARIANTS,
    attach(api) { T.api = api; },
    update,
    setVariant,
    // tests and the report builder (tests/test_threed_view.js, docs/build_threed_report.js)
    debug: () => ({ variant: T.variant, desc: T.desc, scene: T.scene, states: T.states, pinned: T.pinned, hover: T.hover, loading: T.loading,
      sceneError: T.sceneError, live: T.live.size, source: Boolean(T.source) }),
  };
}());
