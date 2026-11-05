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
//
// Three things no CAD has live here too (docs/robot-description.md "From the page"; Zach, 2026-11-04: "I can just upload the URDF"; "how we
// represent like the FDCANUSB in all of this"): the hardware row, one chip per adapter and computer with leader lines to its devices'
// parts; the list of devices not in the model; and Map devices, which puts a device on a part with one click, the 10-minute fallback
// when no camera and no file can say which motor is which.
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
    partsKey: '', chip: null, nimOpen: false, menuOpen: false, map: { on: false, hit: null, levels: null, level: -1, pick: -1, shift: 0, busy: false, said: '' },
    upload: null, notice: '', noticeErr: false, noticeTimer: 0, dragDepth: 0,
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
  const hardware = () => (T.desc && T.desc.hardware) || [];
  const notInModel = () => (T.desc && T.desc.not_in_model) || [];
  const library = () => (T.desc && T.desc.library) || [];

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
  const whereOf = (part) => part.name || [...(part.links || []), ...(part.meshes || [])].join(', ');
  const nameOf = (part) => (part.path ? T.api.entityLabel(part.path) : '') || whereOf(part);
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
            <button type="button" class="td-btn" data-td-map aria-pressed="false" title="Place a device on the model: pick it in the tree, then click its part"><svg viewBox="0 0 24 24"><path d="M12 21s7-6.1 7-11.5A7 7 0 1 0 5 9.500C5 14.900 12 21 12 21z"/><circle cx="12" cy="9.500" r="2.400"/></svg><span>Map devices</span></button>
            <button type="button" class="td-btn" data-td-model title="Bring another robot in, or go back to a kept one"><svg viewBox="0 0 24 24"><path d="m12 2 9 5v10l-9 5-9-5V7z"/><path d="m3 7 9 5 9-5M12 12v10"/></svg><span>Robot</span></button>
          </div>
          <div class="td-menu" hidden></div>
          <div class="td-hw"></div>
          <div class="td-mapbar" hidden></div>
          <svg class="td-lines" aria-hidden="true"></svg>
          <svg class="td-leaders" aria-hidden="true"></svg>
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
      <div class="td-drop" aria-hidden="true"><div>Drop to bring this robot in</div></div>
      <input type="file" class="td-file-zip" accept=".zip,.urdf,.stl,.dae,.obj" multiple hidden>
      <input type="file" class="td-file-folder" webkitdirectory hidden>
    </div>`;
    const root = host.firstElementChild;
    T.el = { host, root, view: $('.td-view', root), lines: $('.td-lines', root), chips: $('.td-chips', root), card: $('.td-card', root), tip: $('.td-tip', root),
      legend: $('.td-legend', root), status: $('.td-status', root), drawer: $('.td-drawer', root), tray: $('.td-tray', root), slot: $('.td-split-slot', root),
      empty: $('.td-empty', root), barTitle: $('.td-bar-t', root), probe: $('.td-probe', root), hw: $('.td-hw', root), leaders: $('.td-leaders', root),
      mapbar: $('.td-mapbar', root), menu: $('.td-menu', root) };
    setupDrop(root);
    T.el.hw.addEventListener('pointerover', (event) => { const chip = event.target.closest('[data-td-hw]'); chipHover(chip ? chip.dataset.tdHw : null); });
    T.el.hw.addEventListener('pointerleave', () => chipHover(null));
    T.el.view.addEventListener('wheel', (event) => {
      if (!T.map.on || !event.shiftKey) return;
      event.preventDefault();
      event.stopPropagation(); // not a zoom
      shiftLevel((event.deltaY || event.deltaX) > 0 ? 1 : -1);
    }, { capture: true, passive: false });
    T.drawerSash = sashOf('drawer', { axis: 'x', def: 320, min: 240, max: () => Math.max(260, root.getBoundingClientRect().width - 360), varName: '--td-drawer-w' });
    // the split (v4) and the tray (v5) share the bottom panel, each with its own remembered height
    T.splitSash = sashOf('split', { axis: 'y', def: 360, min: 140, max: () => Math.max(180, root.getBoundingClientRect().height - 160), varName: '--td-split-h' });
    T.traySash = sashOf('tray', { axis: 'y', def: 150, min: 70, max: () => Math.max(120, root.getBoundingClientRect().height - 160), varName: '--td-tray-h' });
    root.addEventListener('click', onClick);
    root.addEventListener('dblclick', onDouble);
    T.el.card.addEventListener('pointerenter', () => { T.overCard = true; clearTimeout(T.hideTimer); clearTimeout(T.switchTimer); });
    T.el.card.addEventListener('pointerleave', () => { T.overCard = false; if (T.pinned < 0) hideCardSoon(); });
    document.addEventListener('keydown', (event) => {
      if (!T.shown || (event.target.closest && event.target.closest('input, select, textarea, [contenteditable], .xterm'))) return;
      if (event.key === 'Escape' && T.map.on) { setMap(false); return; }
      if (T.map.on && (event.key === '[' || event.key === ']')) { shiftLevel(event.key === '[' ? -1 : 1); return; }
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
    const key = `${T.desc.name}|${T.desc.urdf}`;
    const partsKey = JSON.stringify(parts().map((part) => [part.links, part.meshes, part.at, part.look_at]));
    if (T.loadKey === key) {
      // a device placed or taken off on the page: the same model and camera, the parts dealt out again
      if (T.partsKey !== partsKey && T.scene && T.scene.loaded()) { T.partsKey = partsKey; T.scene.setParts(parts()); T.lastSel = undefined; }
      return;
    }
    if (T.loading) return;
    T.loading = true;
    T.sceneError = '';
    if (T.scene) { T.scene.dispose(); T.scene = null; }
    T.loadKey = key;
    T.partsKey = partsKey;
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
      // WHY read before the load: the scene goes home as it loads, and that pose must not replace the one this page was opened with
      const cam = S().cam;
      const loading = T.scene.load(T.desc.urdf, parts(), { front: (T.desc.view || {}).front, packages: T.desc.packages });
      status();
      const tick = setInterval(status, 200);
      await loading;
      clearInterval(tick);
      if (cam) { S().cam = cam; T.appliedCam = cam; T.scene.setCamera(cam); }
      T.api.replaceNav();
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
    if (T.upload) text = T.upload.text;
    else if (T.notice) text = T.notice;
    else if (T.sceneError) text = `The model did not load: ${T.sceneError}`;
    else if (timings && !timings.done) text = `Loading the model · ${timings.meshes} of ${timings.total || '…'} meshes`;
    el.textContent = text;
    el.hidden = !text;
    el.classList.toggle('err', Boolean(T.sceneError) || (Boolean(T.notice) && T.noticeErr));
    if (T.el.emptyStatus) { T.el.emptyStatus.textContent = T.upload || T.notice ? text : ''; T.el.emptyStatus.classList.toggle('err', T.noticeErr); }
  }
  function notify(text, isError = false) {
    T.notice = text;
    T.noticeErr = isError;
    clearTimeout(T.noticeTimer);
    T.noticeTimer = setTimeout(() => { T.notice = ''; status(); }, isError ? 12000 : 5000);
    status();
  }

  // ------------------------------------------------------------------------------------------- painting ----
  function paint() {
    if (!T.shown || !T.mounted) return;
    const list = parts();
    const selected = selectedParts();
    const lit = litParts();
    const states = list.map((part, index) => ({ ...partState(part), selected: selected.has(index), lit: lit.has(index) }));
    T.states = states;
    if (T.scene) T.scene.setStates(states.map((state) => ({ level: state.level, ghost: state.ghost, selected: state.selected, lit: state.lit })));
    drawHardware();
    drawMapBar();
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
    const timings = T.scene && T.scene.loaded() ? T.scene.timings() : null;
    const html = `<div class="td-leg-rows">${items.map(([level, n]) => `<span class="td-leg td-leg-${level}${n ? '' : ' none'}" title="${n} part${n === 1 ? '' : 's'}: ${words[level]}"><i></i>${words[level]}${n ? ` <b>${n}</b>` : ''}</span>`).join('')}`
      + '<span class="td-leg td-leg-plain" title="Grey: a part no device is mapped to"><i></i>not mapped</span></div>'
      + (timings ? `<div class="td-load" title="Time to the first drawn frame with geometry, and to every mesh loaded">${timings.meshes} meshes · first frame ${(timings.firstFrame / 1000).toFixed(2)} s · all ${(timings.done / 1000).toFixed(2)} s</div>` : '');
    if (T.el.legend.dataset.html !== html) { T.el.legend.dataset.html = html; T.el.legend.innerHTML = html; }
  }

  // the info a card or a drawer shows for an entity: its row, its worst checks (/api/v1/checks) and its State (/api/v1/values)
  function checksOf(path) {
    const entry = T.api.cached('/api/v1/checks', 2000);
    // WHY not the checks that did not run: they say nothing about the part (io_diagnostics/not_run.py), and "unknown" would sort first
    return ((entry.data && entry.data.checks && entry.data.checks[path]) || []).filter((check) => !check.not_run)
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
    if (T.map.on) return; // Map devices has its own tip
    const card = T.el.card;
    const tip = T.el.tip;
    const list = parts();
    // the pointer on the card keeps it (the page redraws every second; the part under the card is no longer hovered)
    const index = T.pinned >= 0 ? T.pinned : T.hover.part >= 0 ? T.hover.part : T.overCard && !card.hidden ? Number(card.dataset.part) : -1;
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
    html += `<div class="td-foot"><span class="td-path" title="${esc(part.note || part.path || '')}">${esc(whereOf(part))}</span>${openButton(part)}</div>`;
    if (T.variant === 'v1') html += `<div class="td-pin">${T.pinned >= 0 ? 'Pinned · Esc or a click elsewhere unpins' : 'Click to pin'}</div>`;
    if (card.dataset.html !== html) { card.dataset.html = html; card.innerHTML = html; }
    card.classList.toggle('rich', T.variant === 'v1');
    card.classList.toggle('pinned', T.pinned >= 0);
    card.dataset.part = String(index);
    card.hidden = false;
    // placed at the cursor while a part is hovered; kept where it is while the pointer is on it, and once pinned
    if (T.hover.part === index && (T.pinned < 0 || !card.dataset.placed)) { place(card, T.hover.x, T.hover.y); card.dataset.placed = T.pinned >= 0 ? '1' : ''; }
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
    T.hideTimer = setTimeout(() => { if (T.pinned < 0 && T.hover.part < 0 && !T.overCard) { T.el.card.hidden = true; } }, 260);
  }

  // v2: callout labels, re-placed on every drawn frame (they follow the camera)
  function drawLayer() {
    if (!T.mounted) return;
    drawLeaders();
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
        + (mapped ? `<div class="td-links" title="${esc(parts()[index].note || '')}">On the model: ${esc(whereOf(parts()[index]))}</div>` : '<div class="td-links muted">Not mapped to a part of the model.</div>')
        + `<div class="td-sec-h">Diagnostics <span>${node.checks.length}</span></div>`
        + (node.checks.length ? checkRows(node.checks.slice().sort((a, b) => (RANK[b.effective_level] ?? -1) - (RANK[a.effective_level] ?? -1)), 40) : '<div class="muted">No checks of its own.</div>')
        + (node.notRun && node.notRun.length ? `<div class="muted">${node.notRun.length} check${node.notRun.length === 1 ? '' : 's'} did not run</div>` : '')
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
  // WHY the wait: on the way from a part to its card's Open the pointer can cross another part; a live card (one that already took the
  // pointer) moves to another part only after the pointer rests there, and stays if the pointer reaches the card first
  function onHover(part, x, y, hit) {
    if (T.map.on) { mapHover(hit, x, y); return; }
    const card = T.el.card;
    const shown = card.hidden ? -1 : Number(card.dataset.part);
    clearTimeout(T.switchTimer);
    if (T.variant !== 'v5' && T.pinned < 0 && !card.hidden && card.classList.contains('live') && part >= 0 && part !== shown) {
      T.switchTimer = setTimeout(() => applyHover(part, x, y), 250);
      return;
    }
    applyHover(part, x, y);
  }
  function applyHover(part, x, y) {
    // WHY a dwell before the card takes the pointer: it sits beside the cursor, so a quick sweep across the model would run into
    // it and stop hovering; it becomes clickable (Open) once the pointer rests on one part
    // a new part restarts the wait; leaving a part (onto the card, off the model) or coming back to the card's own part keeps it
    if (part >= 0 && (T.el.card.hidden || part !== Number(T.el.card.dataset.part))) {
      T.el.card.classList.remove('live');
      clearTimeout(T.liveTimer);
      T.liveTimer = setTimeout(() => T.el.card.classList.add('live'), 350);
    }
    T.hover = { part, x, y };
    if (part >= 0) clearTimeout(T.hideTimer);
    if (T.pinned >= 0 && T.variant === 'v1') return;
    if (part < 0 && T.variant !== 'v5') { hideCardSoon(); return; }
    drawCard();
  }
  function onPick(part, event, hit) {
    if (T.map.on) { mapPick(hit); return; }
    if (part < 0) { if (T.pinned >= 0) { T.pinned = -1; drawCard(); } return; }
    if (T.variant === 'v1') { T.pinned = part; T.el.card.dataset.placed = ''; }
    selectPart(part, false);
    paint();
  }
  function onSceneDouble(part) {
    if (T.map.on) return; // two quick clicks there are a place and an undo
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
    const chip = t.closest('[data-td-hw]');
    if (chip) { selectRow(chip.dataset.tdHw); return; }
    const device = t.closest('[data-td-nim]');
    if (device) { if (device.dataset.tdNim) selectRow(device.dataset.tdNim); return; }
    if (t.closest('[data-td-map]')) { setMap(!T.map.on); return; }
    if (t.closest('[data-td-map-done]')) { setMap(false); return; }
    const widen = t.closest('[data-td-map-level]');
    if (widen) { shiftLevel(Number(widen.dataset.tdMapLevel)); return; }
    if (t.closest('[data-td-model]')) { toggleMenu(); return; }
    const pick = t.closest('[data-td-pick]');
    if (pick) { const input = T.el.root.querySelector(pick.dataset.tdPick === 'folder' ? '.td-file-folder' : '.td-file-zip'); T.menuOpen = false; drawMenu(); input.click(); return; }
    const use = t.closest('[data-td-use]');
    if (use) { descriptionWrite('activate', use.dataset.tdUse); return; }
    const remove = t.closest('[data-td-remove]');
    if (remove) {
      // WHY a second click on the same button and not a browser dialog: nothing else on this page uses one, and an uploaded folder is gone for good
      if (remove.dataset.armed) descriptionWrite('remove', remove.dataset.tdRemove);
      else { remove.dataset.armed = '1'; remove.textContent = 'Remove it?'; }
      return;
    }
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
        <div class="td-dropzone"><b>Drop a URDF folder or a .zip here</b><span>with its meshes; a Cad2Robot bundle brings its own mappings</span>
          <div class="td-dropzone-btns"><button type="button" class="td-btn" data-td-pick="folder">Choose a folder…</button><button type="button" class="td-btn" data-td-pick="zip">Choose a .zip…</button></div>
          <div class="td-empty-status" aria-live="polite"></div></div>
        ${keptHtml()}
        <p>Or ${problem ? 'fix' : 'write'} <code>${esc((desc && desc.file_name) || 'robot-description.toml')}</code> in the config folder${desc && desc.file ? `:</p><p class="td-where"><code>${esc(desc.file)}</code><button type="button" class="td-btn td-copy" data-td-copy="${esc(desc.file)}" title="Copy the path">Copy</button>` : '.'}</p>
        <p>A TOML file: the URDF's path and one <code>[[part]]</code> per device, matched the way <code>expected.toml</code> matches one.</p>
        <pre class="td-example">${esc(EXAMPLE)}</pre>
        <p class="muted">The whole format: <span class="td-docs">${esc((desc && desc.docs) || 'docs/robot-description.md')}</span>
          <button type="button" class="td-btn td-copy" data-td-copy="${esc((desc && desc.docs) || 'docs/robot-description.md')}" title="Copy the path of the docs">Copy</button></p>
      </div>`;
    if (T.el.empty.dataset.html !== html) { T.el.empty.dataset.html = html; T.el.empty.innerHTML = html; T.el.emptyStatus = T.el.empty.querySelector('.td-empty-status'); status(); }
  }
  document.addEventListener('click', (event) => {
    const copy = event.target.closest && event.target.closest('[data-td-copy]');
    if (!copy) return;
    const done = () => { copy.textContent = 'Copied'; setTimeout(() => { copy.textContent = 'Copy'; }, 1400); };
    if (navigator.clipboard) navigator.clipboard.writeText(copy.dataset.tdCopy).then(done, () => {});
  });

  // ------------------------------------------------------------------------------- the chips: what no CAD has ----
  // One chip per adapter and per computer, in its row's colour. Hovered or selected, it lights the parts of its devices and draws a
  // leader line to each (Zach, 2026-11-04: "we can see which motors are where"). WHY chips float here and are not placed in 3D: an
  // adapter hangs off a USB cable somewhere near a PC; a position for it would be invented.
  const levelClass = (row) => `lv-${row ? (row.connected === false ? 'absent' : row.level || 'none') : 'absent'}`;
  const chipItem = (path) => hardware().find((item) => item.path === path) || null;
  function litParts() {
    const item = chipItem(T.chip) || chipItem(S().selKey ? null : S().sel);
    return new Set(item ? item.parts : []);
  }
  function selectRow(path) {
    if (!path || !rowOf(path)) return;
    T.fromScene = true;
    T.api.select(path);
  }
  function drawHardware() {
    const hw = hardware();
    const sel = S().selKey ? null : S().sel;
    const chips = hw.map((item) => {
      const row = rowOf(item.path);
      const count = item.parts.length;
      const word = row ? (row.connected === false ? 'not detected' : row.level || 'no reports') : 'not detected';
      return `<button type="button" class="td-hw-chip ${levelClass(row)}${sel === item.path ? ' sel' : ''}" data-td-hw="${esc(item.path)}" data-kind="${esc(item.kind)}"`
        + ` title="${esc(item.name)} · ${esc(word)} · ${count} device${count === 1 ? '' : 's'} on the model">${T.api.icon(item.kind === 'computer' ? 'host' : 'adapter')}<span>${esc(item.name)}</span><i class="td-dot" aria-hidden="true"></i><small>${count}</small></button>`;
    }).join('');
    const list = notInModel();
    const items = list.map((item) => {
      const row = rowOf(item.path);
      const armed = T.map.on && sel && sel === item.path;
      return `<li><button type="button" class="td-nim-item${sel === item.path ? ' sel' : ''}${armed ? ' armed' : ''}" data-td-nim="${esc(item.path || '')}"${item.path ? '' : ' disabled'} title="${esc(item.reason || 'Nobody has placed this device on the model')}">`
        + `<i class="td-dot ${levelClass(row)}" aria-hidden="true"></i><span>${esc(item.path ? T.api.entityLabel(item.path) || item.name : item.name)}</span><small>${esc(item.path ? (row && row.connected !== false ? row.level || 'no reports' : 'not detected') : 'not detected')}</small></button>`
        + (item.reason ? `<p>${esc(item.reason)}</p>` : '') + '</li>';
    }).join('');
    const nim = list.length || hw.length ? `<details class="td-nim"${T.nimOpen ? ' open' : ''}><summary>Not in the model <b>${list.length}</b></summary>${list.length ? `<ul>${items}</ul>` : '<p class="td-nim-none">Every device is on the model.</p>'}</details>` : '';
    const html = chips + nim;
    if (T.el.hw.dataset.html !== html) {
      T.el.hw.dataset.html = html;
      T.el.hw.innerHTML = html;
      const details = T.el.hw.querySelector('.td-nim');
      if (details) details.addEventListener('toggle', () => { T.nimOpen = details.open; });
    }
    drawLeaders();
  }
  // re-placed on every drawn frame: the lines follow the camera
  function drawLeaders() {
    const svg = T.el.leaders;
    if (!svg) return;
    const item = chipItem(T.chip) || chipItem(S().selKey ? null : S().sel);
    const chip = item && T.scene && T.scene.loaded() ? T.el.hw.querySelector(`[data-td-hw="${CSS.escape(item.path)}"]`) : null;
    let body = '';
    if (chip) {
      const view = T.el.view.getBoundingClientRect();
      const from = chip.getBoundingClientRect();
      const x1 = from.left + from.width / 2 - view.left;
      const y1 = from.bottom - view.top;
      for (const index of item.parts) {
        const at = T.scene.project(index);
        if (!at || !at.inside) continue;
        const state = (T.states || [])[index] || {};
        const level = state.ghost ? 'absent' : problemOf(state) || 'none';
        const x2 = at.x - view.left;
        const y2 = at.y - view.top;
        body += `<line x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}" class="lv-${level}" data-part="${index}"/>`
          + `<circle cx="${x2.toFixed(1)}" cy="${y2.toFixed(1)}" r="3.5" class="lv-${level}"/>`;
      }
    }
    if (svg.dataset.body !== body) { svg.dataset.body = body; svg.innerHTML = body; }
  }
  function chipHover(path) {
    if (T.chip === path) return;
    T.chip = path;
    paint();
    if (T.scene) T.scene.request();
  }

  // ------------------------------------------------------------------------------------------- Map devices ----
  // The tree's selection (or a row of the list) is the device; a click on the model puts it there, a second click on the same body takes
  // it off. WHY the click saves at once and there is no Save: with the adapter's wiggle-each-board utility a person walks the rack and
  // clicks each motor as it moves; a step per motor would be the slow part.
  function mapDevice() {
    const sel = S().selKey ? null : S().sel;
    return sel ? rowOf(sel) : null;
  }
  function setMap(on) {
    T.map = { ...T.map, on, hit: null, levels: null, level: -1, pick: -1, shift: 0, said: '' };
    T.pinned = -1;
    if (T.scene) T.scene.setCandidate([]);
    if (T.el.card) T.el.card.hidden = true;
    if (T.el.tip) T.el.tip.hidden = true;
    T.el.root.classList.toggle('td-mapping', on);
    const button = T.el.root.querySelector('[data-td-map]');
    button.setAttribute('aria-pressed', String(on));
    if (on) { T.nimOpen = true; } // the devices that still need a place, in view
    paint();
  }
  function ownerOf(level) {
    const index = parts().findIndex((part) => (level.kind === 'link' ? (part.links || []).includes(level.link) : (part.meshes || []).includes(level.pattern)));
    return index >= 0 ? parts()[index] : null;
  }
  function mapTip(x, y) {
    const tip = T.el.tip;
    const level = T.map.levels && T.map.levels[T.map.level];
    if (!level) { tip.hidden = true; return; }
    const owner = ownerOf(level);
    const device = mapDevice();
    const same = owner && device && owner.path === device.path;
    tip.textContent = `${level.label} · ${level.meshes.length} mesh${level.meshes.length === 1 ? '' : 'es'}${owner ? ` · now ${nameOf(owner)}` : ''}${device ? (same ? ' · click takes it off' : ' · click places it') : ''} · Shift+wheel: size`;
    tip.hidden = false;
    place(tip, x, y);
  }
  function mapLevel() {
    const levels = T.map.levels;
    T.map.level = levels ? Math.max(0, Math.min(levels.length - 1, T.map.pick + T.map.shift)) : -1;
    T.scene.setCandidate(levels ? levels[T.map.level].meshes : []);
    mapTip(T.map.x, T.map.y);
    drawMapBar();
  }
  function mapHover(hit, x, y) {
    const key = hit ? hit.mesh : -1;
    T.map.x = x;
    T.map.y = y;
    if (key === T.map.hit && T.map.levels) { mapTip(x, y); return; }
    T.map.hit = key;
    const found = hit && T.scene ? T.scene.levelsOf(hit.mesh) : { levels: [], pick: -1 };
    T.map.levels = found.levels.length ? found.levels : null;
    T.map.pick = found.pick;
    mapLevel();
  }
  // WHY the step is kept across bodies and not per hover: the pointer leaves the body to reach Narrower / Wider, and a person placing eight
  // motors wants the same size of piece for all of them
  function shiftLevel(step) {
    T.map.shift = Math.max(-6, Math.min(6, T.map.shift + step));
    mapLevel();
  }
  async function mapPick(hit) {
    if (!hit || !T.map.levels || T.map.busy) return;
    const device = mapDevice();
    if (!device) { T.map.said = 'Pick a device first: a row in the tree, or the list.'; drawMapBar(); return; }
    const level = T.map.levels[T.map.level];
    const body = { path: device.path, ...(level.kind === 'link' ? { links: [level.link] } : { meshes: [level.pattern] }) };
    T.map.busy = true;
    try {
      const response = await fetch('/api/v1/robot-description/assign', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify(body) });
      const answer = await response.json();
      if (!response.ok) throw new Error(answer.error || `HTTP ${response.status}`);
      T.map.said = answer.assigned ? `${T.api.entityLabel(device.path) || device.name} is on ${level.label}. Click it again to undo.` : `${T.api.entityLabel(device.path) || device.name} taken off ${level.label}.`;
      wantDescription(true);
    } catch (error) {
      T.map.said = `Not saved: ${error.message}`;
    }
    T.map.busy = false;
    drawMapBar();
  }
  function drawMapBar() {
    const bar = T.el.mapbar;
    if (!bar) return;
    bar.hidden = !T.map.on;
    if (!T.map.on) return;
    const device = mapDevice();
    const html = `<span class="td-map-t">${device ? `Placing <b>${esc(T.api.entityLabel(device.path) || device.name)}</b>: click its part` : 'Pick a device in the tree or the list, then click its part'}</span>`
      + `<span class="td-map-pick"><button type="button" class="td-btn" data-td-map-level="-1" title="Click a smaller piece ( [ or Shift+wheel )">Narrower</button>`
      + `<button type="button" class="td-btn" data-td-map-level="1" title="Click a bigger piece ( ] or Shift+wheel )">Wider</button>${T.map.shift ? `<small>${T.map.shift > 0 ? '+' : ''}${T.map.shift}</small>` : ''}</span>`
      + '<button type="button" class="td-btn" data-td-map-done>Done</button>'
      + `<span class="td-map-said" aria-live="polite">${esc(T.map.said)}</span>`;
    if (bar.dataset.html !== html) { bar.dataset.html = html; bar.innerHTML = html; }
  }

  // ------------------------------------------------------------------------------- bringing a robot in, kept ones ----
  function keptHtml() {
    const kept = library();
    if (!kept.length) return '';
    return `<div class="td-kept"><h3>Kept on this PC</h3>${kept.map((item) => `<div class="td-kept-row${item.active ? ' on' : ''}"><span class="td-kept-n">${esc(item.name)}</span>`
      + `<small>${esc(item.robot || '')}${item.parts ? ` · ${item.parts} device${item.parts === 1 ? '' : 's'}` : ''}</small>`
      + `${item.active ? '<em>showing</em>' : `<button type="button" class="td-btn" data-td-use="${esc(item.name)}">Show</button>`}`
      + `<button type="button" class="td-btn td-kept-x" data-td-remove="${esc(item.name)}" title="Remove it from this PC">Remove</button></div>`).join('')}</div>`;
  }
  function drawMenu() {
    const menu = T.el.menu;
    menu.hidden = !T.menuOpen;
    if (!T.menuOpen) return;
    menu.innerHTML = `<div class="td-menu-t">Bring a robot in</div><p>Drop a URDF folder or a .zip anywhere on this view, or:</p>
      <div class="td-dropzone-btns"><button type="button" class="td-btn" data-td-pick="folder">Choose a folder…</button><button type="button" class="td-btn" data-td-pick="zip">Choose a .zip…</button></div>${keptHtml()}`;
  }
  function toggleMenu() { T.menuOpen = !T.menuOpen; wantDescription(true); drawMenu(); }
  async function descriptionWrite(op, name) {
    try {
      const response = await fetch(`/api/v1/robot-description/${op}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' }, body: JSON.stringify({ name }) });
      const answer = await response.json();
      if (!response.ok) throw new Error(answer.error || `HTTP ${response.status}`);
      S().cam = null;
      T.menuOpen = false;
      drawMenu();
      notify(op === 'activate' ? `Showing ${name}` : `Removed ${name}`);
    } catch (error) { notify(`Not done: ${error.message}`, true); }
    wantDescription(true);
  }
  // every file under a dropped folder, with its path inside the folder
  function walkEntry(entry, prefix, out) {
    return new Promise((resolve) => {
      if (entry.isFile) { entry.file((file) => { out.push({ file, path: `${prefix}${file.name}` }); resolve(); }, () => resolve()); return; }
      const reader = entry.createReader();
      const next = () => reader.readEntries(async (batch) => {
        if (!batch.length) { resolve(); return; }
        for (const child of batch) await walkEntry(child, `${prefix}${entry.name}/`, out); // eslint-disable-line no-await-in-loop
        next();
      }, () => resolve());
      next();
    });
  }
  async function droppedFiles(transfer) {
    const entries = [...(transfer.items || [])].map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null)).filter(Boolean);
    if (!entries.length) return [...(transfer.files || [])].map((file) => ({ file, path: file.name }));
    const out = [];
    for (const entry of entries) await walkEntry(entry, '', out); // eslint-disable-line no-await-in-loop
    return out;
  }
  function send(body, contentType, hint) {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('POST', `/api/v1/robot-description/upload${hint ? `?name=${encodeURIComponent(hint)}` : ''}`);
      request.setRequestHeader('X-IO-Diagnostics', 'ui');
      if (contentType) request.setRequestHeader('Content-Type', contentType);
      request.upload.onprogress = (event) => { if (event.lengthComputable) { T.upload = { text: event.loaded < event.total ? `Bringing it in · ${Math.round(100 * event.loaded / event.total)} %` : 'Unpacking…' }; status(); } };
      request.onload = () => {
        let answer = {};
        try { answer = JSON.parse(request.responseText); } catch (_) { /* below */ }
        if (request.status === 200) resolve(answer); else reject(new Error(answer.error || `HTTP ${request.status}`));
      };
      request.onerror = () => reject(new Error('the upload did not reach the host'));
      request.send(body);
    });
  }
  async function bringIn(files) {
    if (!files.length || T.upload) return;
    T.upload = { text: 'Bringing it in…' };
    T.menuOpen = false;
    drawMenu();
    status();
    try {
      let answer;
      const zip = files.length === 1 && /\.zip$/i.test(files[0].path) ? files[0].file : null;
      if (zip) answer = await send(zip, 'application/zip', '');
      else {
        const form = new FormData();
        for (const { file, path } of files) form.append('file', file, path);
        answer = await send(form, '', '');
      }
      T.upload = null;
      S().cam = null;
      const missing = (answer.missing_meshes || []).length;
      notify(`${answer.robot || answer.name} is in · ${answer.meshes} mesh${answer.meshes === 1 ? '' : 'es'}${missing ? ` · ${missing} missing` : ''}${answer.mapped ? ` · ${answer.mapped} device${answer.mapped === 1 ? '' : 's'} mapped from its robot.json` : ''}`, missing > 0);
      wantDescription(true);
    } catch (error) {
      T.upload = null;
      notify(`Not brought in: ${error.message}`, true);
    }
  }
  function setupDrop(root) {
    const carries = (event) => [...((event.dataTransfer && event.dataTransfer.types) || [])].includes('Files');
    root.addEventListener('dragenter', (event) => { if (carries(event)) { T.dragDepth += 1; root.classList.add('td-dropping'); event.preventDefault(); } });
    root.addEventListener('dragover', (event) => { if (carries(event)) event.preventDefault(); });
    root.addEventListener('dragleave', (event) => { if (carries(event)) { T.dragDepth = Math.max(0, T.dragDepth - 1); if (!T.dragDepth) root.classList.remove('td-dropping'); } });
    root.addEventListener('drop', (event) => {
      if (!carries(event)) return;
      event.preventDefault();
      T.dragDepth = 0;
      root.classList.remove('td-dropping');
      droppedFiles(event.dataTransfer).then(bringIn);
    });
    root.querySelector('.td-file-zip').addEventListener('change', (event) => { const files = [...event.target.files].map((file) => ({ file, path: file.name })); event.target.value = ''; bringIn(files); });
    root.querySelector('.td-file-folder').addEventListener('change', (event) => { const files = [...event.target.files].map((file) => ({ file, path: file.webkitRelativePath || file.name })); event.target.value = ''; bringIn(files); });
  }

  // ---------------------------------------------------------------------------------------- the page's seam ----
  // app.js calls update(on) after every render; on = the 3D view is the page's view
  function update(on) {
    if (!T.api) return;
    if (T.el.picker) T.el.picker.hidden = !on;
    if (!on) {
      if (T.shown) {
        if (T.map.on) setMap(false);
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
    if (!ready) {
      if (T.map.on) setMap(false);
      if (T.scene) { T.scene.dispose(); T.scene = null; T.loadKey = ''; T.partsKey = ''; }
      drawEmpty();
      placeInspector(false);
      closeLive();
      return;
    }
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
      sceneError: T.sceneError, live: T.live.size, source: Boolean(T.source), overCard: Boolean(T.overCard), map: T.map, chip: T.chip }),
  };
}());
