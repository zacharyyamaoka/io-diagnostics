// r3 · Runs as jobs (docs/runs-v1.md section 7), the GitHub Actions / Jenkins model.
//
// A Runs panel above the status bar: on the left every run on this host, newest first, filterable by device; on the right
// the selected one as a job. Its header says what ran, on what, when, how it ended (state, exit code, duration) and has
// Stop (while it goes), Run again (the normal dry run -> confirm, never a silent re-run) and the recording. Under it are its
// steps: Preconditions (from the dry run's plan), Run (the command, its live terminal), Exit (the code, the plugin's
// stop, the records). Each step's log folds; the live tail is open on the step that is going.
//
// WHY the steps are three and not the plan's own list: the plan's steps are what the plugin WOULD do; the host runs one
// process, so the faithful job is the gate before it (preconditions), the process (one terminal) and its end. The plan's
// steps show in the Preconditions log, from the dry run the person read.
// WHY the plan is kept per run in this browser (ctx.pref): a run's JSON does not carry its dry run; the page that started it
// does. A run from another window, or before this browser kept plans, says so instead of inventing one.
// WHY Run again drives the shell's own Utilities menu: the contract has no "start a dry run" hook, and the person must read
// the sheet before anything runs; clicking the shell's own controls is exactly that flow.
'use strict';
(function () {
  const ID = 'r3';
  const DEFAULT_HEIGHT = 380;
  const MIN_HEIGHT = 160;
  const DEFAULT_LIST = 300;
  const MIN_LIST = 200;
  const DEFAULT_LOG = 260;
  const PLAN_KEEP = 40;
  const CSS = `
#runs-root.r3 { position: relative; flex: none; display: flex; flex-direction: column; height: var(--r3-h, ${DEFAULT_HEIGHT}px); border-top: 1px solid var(--io-border); background: var(--io-surface); min-height: 0; }
#runs-root.r3[hidden] { display: none; }
.r3-sash { position: absolute; left: 0; right: 0; top: -4px; height: 8px; z-index: 5; cursor: row-resize; touch-action: none; outline: none; }
.r3-sash::before { content: ""; position: absolute; left: 0; right: 0; top: 3px; height: 1px; background: transparent; transition: background .1s, height .1s, top .1s; }
.r3-sash:hover::before, .r3-sash:focus-visible::before, body.resizing .r3-sash::before { top: 2px; height: 3px; background: var(--io-selected-line); }
.r3-bar { flex: none; display: flex; align-items: center; gap: 8px; min-height: 34px; padding: 0 6px 0 14px; background: var(--io-surface-2); border-bottom: 1px solid var(--io-border-soft); }
.r3-bar h2 { margin: 0; font: 600 12.5px/1 var(--io-font-ui); color: var(--io-text-strong); }
.r3-bar .r3-sp { flex: 1; }
.r3-count { color: var(--io-faint); font-size: 11.5px; }
.r3-iconbtn { flex: none; width: 24px; height: 24px; display: inline-flex; align-items: center; justify-content: center; border: 0; background: transparent; color: var(--io-faint); border-radius: var(--io-radius-sm); cursor: pointer; padding: 0; }
.r3-iconbtn:hover { background: var(--io-hover); color: var(--io-text); }
.r3-cols { flex: 1; min-height: 0; display: flex; }
.r3-list { flex: none; width: var(--r3-list-w, ${DEFAULT_LIST}px); min-width: 0; display: flex; flex-direction: column; background: var(--io-surface); }
.r3-filter { flex: none; display: flex; align-items: center; gap: 6px; padding: 8px 10px; border-bottom: 1px solid var(--io-border-soft); }
.r3-filter select { flex: 1; min-width: 0; font: inherit; font-size: 12px; color: var(--io-text); background: var(--io-field); border: 1px solid var(--io-border); border-radius: var(--io-radius-sm); padding: 4px 6px; }
.r3-filter select:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.r3-rows { flex: 1; min-height: 0; overflow: auto; outline: none; }
.r3-row { display: flex; align-items: flex-start; gap: 9px; padding: 8px 12px; border-bottom: 1px solid var(--io-border-soft); cursor: pointer; }
.r3-row:hover { background: var(--io-hover); }
.r3-row[aria-selected="true"] { background: var(--io-selected); box-shadow: inset 2px 0 0 var(--io-selected-line); }
.r3-rows:focus-visible .r3-row[aria-selected="true"] { box-shadow: inset 2px 0 0 var(--io-selected-line), inset 0 0 0 2px var(--io-focus); }
.r3-st { flex: none; width: 14px; height: 14px; margin-top: 1px; display: inline-flex; align-items: center; justify-content: center; }
.r3-st .shape { width: 14px; height: 14px; }
.r3-tx { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 1px; }
.r3-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-text-strong); font-weight: 500; }
.r3-m { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-faint); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.r3-none { padding: 18px 14px; color: var(--io-faint); font-size: 12.5px; }
.r3-vsash { flex: none; width: 1px; background: var(--io-border-soft); position: relative; cursor: col-resize; touch-action: none; outline: none; }
.r3-vsash::before { content: ""; position: absolute; top: 0; bottom: 0; left: -4px; right: -4px; }
.r3-vsash:hover, .r3-vsash:focus-visible, .r3-vsash.drag { background: var(--io-selected-line); box-shadow: 0 0 0 1px var(--io-selected-line); }
.r3-job { flex: 1; min-width: 0; overflow: auto; padding: 14px 18px 18px; }
.r3-job-h { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.r3-job-h h3 { margin: 0; font: 600 15px/1.3 var(--io-font-display); color: var(--io-text-strong); }
.r3-pill { display: inline-flex; align-items: center; gap: 5px; padding: 1px 8px; border-radius: var(--io-radius-pill); background: var(--io-control); color: var(--io-text); font-size: 11.5px; font-weight: 500; }
.r3-pill.err { background: var(--io-error-soft); color: var(--io-error-text); }
.r3-pill.warn { background: var(--io-warn-soft); color: var(--io-warn-text); }
.r3-pill .shape { width: 11px; height: 11px; vertical-align: 0; }
.r3-job-h .r3-sp { flex: 1; }
.r3-btns { display: flex; gap: 6px; flex-wrap: wrap; }
.r3-btn { border: 1px solid var(--io-border); background: var(--io-field); color: var(--io-text); border-radius: var(--io-radius-sm); padding: 4px 11px; font: inherit; font-size: 12px; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; }
.r3-btn:hover:not([disabled]) { background: var(--io-hover); }
.r3-btn:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.r3-btn[disabled] { opacity: .55; cursor: default; }
.r3-btn.moves { background: var(--io-text-strong); border-color: var(--io-text-strong); color: var(--io-surface); }
.r3-btn.moves:hover:not([disabled]) { background: var(--io-text); }
.r3-btn[hidden] { display: none; }
.r3-facts { display: flex; flex-wrap: wrap; gap: 3px 18px; margin: 8px 0 12px; max-width: 900px; }
.r3-fact { min-width: 0; display: flex; align-items: baseline; gap: 5px; }
.r3-fact dt { color: var(--io-faint); font-size: 10.5px; text-transform: uppercase; letter-spacing: .5px; }
.r3-fact dd { margin: 0; max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
.r3-fact dd.mono { font-family: var(--io-font-mono); font-size: 11.5px; }
.r3-note { margin: 0 0 10px; color: var(--io-muted); font-size: 12px; }
.r3-steps { display: flex; flex-direction: column; border: 1px solid var(--io-border-soft); border-radius: var(--io-radius); overflow: hidden; max-width: 900px; }
.r3-step + .r3-step { border-top: 1px solid var(--io-border-soft); }
.r3-step-h { width: 100%; display: flex; align-items: center; gap: 9px; padding: 8px 12px; border: 0; background: var(--io-surface-2); color: var(--io-text); font: inherit; text-align: left; cursor: pointer; }
.r3-step-h:hover { background: var(--io-hover); }
.r3-step-h:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: -2px; }
.r3-chev { width: 12px; height: 12px; flex: none; fill: none; stroke: var(--io-faint); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; transition: transform .1s; }
.r3-step-h[aria-expanded="true"] .r3-chev { transform: rotate(90deg); }
.r3-step-n { font-weight: 600; color: var(--io-text-strong); }
.r3-step-s { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-muted); font-family: var(--io-font-mono); font-size: 11.5px; }
.r3-step-r { flex: none; color: var(--io-faint); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.r3-step-b { padding: 10px 12px 12px; background: var(--io-surface); border-top: 1px solid var(--io-border-soft); }
.r3-step-b[hidden] { display: none; }
.r3-pre { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; font-size: 12.5px; }
.r3-pre li { display: flex; gap: 8px; }
.r3-pre .ok { color: var(--io-ok-text); }
.r3-pre .st { flex: none; width: 14px; text-align: center; color: var(--io-muted); }
.r3-pre .why { color: var(--io-faint); }
.r3-plan { margin: 8px 0 0; padding-left: 18px; font-size: 12px; color: var(--io-muted); }
.r3-plan code, .r3-code { font-family: var(--io-font-mono); font-size: 11.5px; word-break: break-all; }
.r3-log { height: var(--r3-log-h, ${DEFAULT_LOG}px); border: 1px solid var(--io-border-soft); border-radius: var(--io-radius-sm); background: var(--io-surface); padding: 6px 4px 2px 10px; overflow: hidden; }
.r3-log .xterm { height: 100%; }
.r3-log .xterm-viewport { background: transparent !important; }
.r3-grip { height: 8px; cursor: row-resize; touch-action: none; outline: none; position: relative; }
.r3-grip::before { content: ""; position: absolute; left: 40%; right: 40%; top: 3px; height: 2px; border-radius: 1px; background: var(--io-border-soft); transition: background .1s; }
.r3-grip:hover::before, .r3-grip:focus-visible::before, .r3-grip.drag::before { background: var(--io-selected-line); }
.r3-exit { display: grid; gap: 3px; font-size: 12.5px; }
.r3-exit .mono { font-family: var(--io-font-mono); font-size: 12px; }
.r3-exit .bad { color: var(--io-error-text); }
.r3-spin { width: 9px; height: 9px; border-radius: 50%; border: 2px solid var(--io-border); border-top-color: var(--io-text); animation: r3-spin 0.9s linear infinite; }
.r3-spin.wait { animation: none; border-top-color: var(--io-border); border-style: dotted; }
@keyframes r3-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .r3-spin { animation: none; } }
.r3-tick, .r3-dash { width: 14px; height: 14px; fill: none; stroke: var(--io-muted); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.r3-toggle { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: var(--io-muted); font: inherit; font-size: 12px; padding: 2px 7px; border-radius: var(--io-radius-sm); cursor: pointer; }
.r3-toggle:hover { background: var(--io-hover); color: var(--io-text); }
.r3-toggle[aria-pressed="true"] { color: var(--io-text-strong); }
.r3-toggle .ic { width: 13px; height: 13px; }
.r3-badge { min-width: 15px; height: 15px; padding: 0 4px; border-radius: 8px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10.5px; font-weight: 600; line-height: 15px; text-align: center; }
.r3-badge[hidden] { display: none; }
`;

  const tickSvg = '<svg class="r3-tick" viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8.5 3 3 6-7"/></svg>';
  const dashSvg = '<svg class="r3-dash" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 8h8"/></svg>';
  const chevSvg = '<svg class="r3-chev" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3.5 4.5 4.5L6 12.5"/></svg>';
  const closeSvg = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>';

  const view = {
    name: 'Runs as jobs',
    description: 'A Runs list (newest first, filter by device) and, for the selected run, its job: header, the dry run\'s preconditions, the command with its live log, the exit; Run again goes through the dry run',
    order: 3,

    mount(ctx) {
      const s = { ctx, selected: ctx.pref('r3.selected', '') || null, device: ctx.pref('r3.device', ''), open: ctx.pref('r3.open', '0') === '1', stopping: new Set(), plans: new Map(),
        steps: new Map(), height: Number(ctx.pref('r3.height', String(DEFAULT_HEIGHT))) || DEFAULT_HEIGHT, listWidth: Number(ctx.pref('r3.list', String(DEFAULT_LIST))) || DEFAULT_LIST,
        logHeight: Number(ctx.pref('r3.log', String(DEFAULT_LOG))) || DEFAULT_LOG, off: [], timer: null, detail: null, listSig: '', note: '', noteRun: '' };
      this.s = s;
      ctx.css('r3', CSS);
      const root = ctx.root;
      root.classList.add('r3');
      root.innerHTML = `<div class="r3-sash" role="separator" aria-orientation="horizontal" aria-label="Resize the Runs panel" tabindex="0" title="Drag to resize · double-click to reset"></div>`
        + `<div class="r3-bar"><h2>Runs</h2><span class="r3-count"></span><span class="r3-sp"></span><button class="r3-iconbtn r3-hide" title="Hide the panel" aria-label="Hide the Runs panel">${closeSvg}</button></div>`
        + `<div class="r3-cols"><aside class="r3-list"><div class="r3-filter"><select aria-label="Filter runs by device"></select></div>`
        + `<div class="r3-rows" role="listbox" aria-label="Runs, newest first" tabindex="0"></div></aside>`
        + `<div class="r3-vsash" role="separator" aria-orientation="vertical" aria-label="Resize the list" tabindex="0" title="Drag to resize · double-click to reset"></div>`
        + `<section class="r3-job" aria-label="Run"></section></div>`;
      s.el = { sash: root.querySelector('.r3-sash'), count: root.querySelector('.r3-count'), select: root.querySelector('.r3-filter select'), rows: root.querySelector('.r3-rows'),
        vsash: root.querySelector('.r3-vsash'), job: root.querySelector('.r3-job'), hide: root.querySelector('.r3-hide') };
      ctx.statusSlot.innerHTML = `<button class="r3-toggle" aria-pressed="false" aria-expanded="false" aria-controls="runs-root" title="Runs: every run on this host, as jobs">`
        + `<svg class="ic" aria-hidden="true"><use href="#i-tty"/></svg><span>Runs</span><span class="r3-badge" hidden></span></button>`;
      s.el.toggle = ctx.statusSlot.querySelector('.r3-toggle');
      s.el.badge = s.el.toggle.querySelector('.r3-badge');
      this.applySizes();
      this.wire();
      s.off.push(ctx.onChange(() => this.update(ctx)));
      s.timer = setInterval(() => this.update(ctx), 1000);
      this.update(ctx);
    },

    unmount(ctx) {
      const s = this.s;
      if (!s) return;
      clearInterval(s.timer);
      s.off.forEach((off) => off());
      this.dropDetail();
      window.removeEventListener('resize', s.onResize);
      document.body.classList.remove('resizing');
      ctx.root.classList.remove('r3');
      for (const name of ['--r3-h', '--r3-list-w', '--r3-log-h']) ctx.root.style.removeProperty(name);
      ctx.root.innerHTML = '';
      ctx.root.hidden = true;
      ctx.statusSlot.innerHTML = '';
      this.s = null;
    },

    // ------------------------------------------------------------------------------------------ the seam ----
    // Show a run: the panel opens on it.
    open(id, ctx) {
      const s = this.s;
      if (!s || !ctx.run(id)) return;
      s.selected = id;
      ctx.setPref('r3.selected', id);
      if (s.device && (ctx.run(id) || {}).device_id !== s.device) { s.device = ''; ctx.setPref('r3.device', ''); }
      this.setOpen(true);
      this.update(ctx);
    },

    // WHY the plan is kept here: this is the one hook that sees the util (and its dry run) the run was started from.
    // The line itself stays the shell's (null).
    runLine(util, run, ctx) {
      const s = this.s;
      if (s && util && util.dry && util.runId && !s.plans.has(util.runId) && !ctx.pref(`r3.plan.${util.runId}`, '')) this.keepPlan(util);
      return null;
    },

    update(ctx) {
      const s = this.s;
      if (!s) return;
      const runs = ctx.runs();
      // a run that starts shows the panel only through open() (the shell opens it for anything that changes or moves a device)
      const known = new Set(runs.map((run) => run.id));
      if (s.selected && !known.has(s.selected) && runs.length) s.selected = null;
      if (!s.selected && runs.length) {
        const going = runs.find((run) => run.active);
        s.selected = (going || runs[0]).id;
      }
      this.paintList(runs);
      const going = runs.filter((run) => run.active).length;
      s.el.badge.hidden = !going;
      s.el.badge.textContent = String(going);
      s.el.toggle.title = going ? `Runs: ${going} going` : 'Runs: every run on this host, as jobs';
      s.ctx.root.hidden = !s.open;
      s.el.toggle.setAttribute('aria-pressed', String(s.open));
      s.el.toggle.setAttribute('aria-expanded', String(s.open));
      if (s.open) this.paintJob();
    },

    // ------------------------------------------------------------------------------------------ wiring ----
    wire() {
      const s = this.s;
      const { ctx } = s;
      const root = ctx.root;
      s.el.toggle.addEventListener('click', () => this.setOpen(!s.open));
      s.el.hide.addEventListener('click', () => this.setOpen(false));
      s.el.select.addEventListener('change', () => { s.device = s.el.select.value; ctx.setPref('r3.device', s.device); s.listSig = ''; this.update(ctx); });
      s.el.rows.addEventListener('click', (event) => {
        const row = event.target.closest('[data-r3-run]');
        if (row) this.select(row.dataset.r3Run);
      });
      s.el.rows.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
        const ids = [...s.el.rows.querySelectorAll('[data-r3-run]')].map((row) => row.dataset.r3Run);
        const at = ids.indexOf(s.selected);
        const next = ids[Math.max(0, Math.min(ids.length - 1, at + (event.key === 'ArrowDown' ? 1 : -1)))];
        if (next) { this.select(next); const row = s.el.rows.querySelector(`[data-r3-run="${next}"]`); if (row) row.scrollIntoView({ block: 'nearest' }); }
        event.preventDefault();
      });
      root.addEventListener('click', (event) => {
        const stop = event.target.closest('[data-r3-stop]');
        if (stop) { this.stop(stop.dataset.r3Stop); return; }
        const again = event.target.closest('[data-r3-again]');
        if (again) { this.runAgain(again.dataset.r3Again); return; }
        const toggle = event.target.closest('[data-r3-step]');
        if (toggle) this.toggleStep(toggle.dataset.r3Step);
      });
      // the three boundaries: the panel's top edge, the list's right edge, the log's bottom edge — each drags, is remembered,
      // resets on double-click and moves by arrow keys when focused (Zach's standing rule)
      this.sash(s.el.sash, { axis: 'y', min: MIN_HEIGHT, max: () => window.innerHeight - 160, get: () => s.height, apply: (value, persist) => this.applyHeight(value, persist), reset: () => this.applyHeight(DEFAULT_HEIGHT, 'reset'),
        delta: (event, start) => start.value - (event.clientY - start.y) });
      this.sash(s.el.vsash, { axis: 'x', min: MIN_LIST, max: () => Math.max(MIN_LIST, root.clientWidth - 360), get: () => s.listWidth, apply: (value, persist) => this.applyList(value, persist), reset: () => this.applyList(DEFAULT_LIST, 'reset'),
        delta: (event, start) => start.value + (event.clientX - start.x) });
      window.addEventListener('resize', s.onResize = () => { this.applyHeight(s.height, false); this.applyList(s.listWidth, false); });
    },

    // One boundary: pointer drag with capture, double-click reset, arrow keys.
    sash(el, spec) {
      let drag = null;
      el.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        drag = { id: event.pointerId, x: event.clientX, y: event.clientY, value: spec.get() };
        el.setPointerCapture(event.pointerId);
        el.classList.add('drag');
        document.body.classList.add('resizing');
        event.preventDefault();
      });
      el.addEventListener('pointermove', (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        drag.next = spec.apply(Math.round(Math.max(spec.min, Math.min(spec.delta(event, drag), spec.max()))), false);
      });
      const end = (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        if (drag.next !== undefined) spec.apply(drag.next, true);
        drag = null;
        el.classList.remove('drag');
        document.body.classList.remove('resizing');
        this.fitLog();
      };
      el.addEventListener('pointerup', end);
      el.addEventListener('pointercancel', end);
      el.addEventListener('dblclick', () => { spec.reset(); this.fitLog(); });
      // the arrow that makes the thing bigger: Up for the panel's top edge, Right for the list's right edge
      el.addEventListener('keydown', (event) => {
        const grow = spec.axis === 'y' ? 'ArrowUp' : 'ArrowRight';
        const shrink = spec.axis === 'y' ? 'ArrowDown' : 'ArrowLeft';
        if (event.key !== grow && event.key !== shrink) return;
        spec.apply(Math.round(Math.max(spec.min, Math.min(spec.get() + (event.key === grow ? 16 : -16), spec.max()))), true);
        this.fitLog();
        event.preventDefault();
      });
    },

    applyHeight(height, persist) {
      const s = this.s;
      const value = Math.round(Math.max(MIN_HEIGHT, Math.min(height, Math.max(MIN_HEIGHT, window.innerHeight - 160))));
      s.height = value;
      s.ctx.root.style.setProperty('--r3-h', `${value}px`);
      if (persist === 'reset') s.ctx.setPref('r3.height', ''); else if (persist) s.ctx.setPref('r3.height', String(value));
      return value;
    },
    applyList(width, persist) {
      const s = this.s;
      const value = Math.round(Math.max(MIN_LIST, Math.min(width, Math.max(MIN_LIST, s.ctx.root.clientWidth - 360))));
      s.listWidth = value;
      s.ctx.root.style.setProperty('--r3-list-w', `${value}px`);
      if (persist === 'reset') s.ctx.setPref('r3.list', ''); else if (persist) s.ctx.setPref('r3.list', String(value));
      return value;
    },
    applyLog(height, persist) {
      const s = this.s;
      const value = Math.round(Math.max(80, Math.min(height, Math.max(120, window.innerHeight - 260))));
      s.logHeight = value;
      s.ctx.root.style.setProperty('--r3-log-h', `${value}px`);
      if (persist === 'reset') s.ctx.setPref('r3.log', ''); else if (persist) s.ctx.setPref('r3.log', String(value));
      return value;
    },
    applySizes() {
      const s = this.s;
      this.applyHeight(s.height, false);
      s.ctx.root.style.setProperty('--r3-list-w', `${s.listWidth}px`);
      s.ctx.root.style.setProperty('--r3-log-h', `${s.logHeight}px`);
    },
    fitLog() { const s = this.s; if (s.detail && s.detail.bound) s.detail.bound.fit(); },

    setOpen(open, remember = true) {
      const s = this.s;
      s.open = open;
      if (remember) s.ctx.setPref('r3.open', open ? '1' : '0');
      s.ctx.root.hidden = !open;
      s.el.toggle.setAttribute('aria-pressed', String(open));
      s.el.toggle.setAttribute('aria-expanded', String(open));
      if (open) { this.paintJob(); requestAnimationFrame(() => this.fitLog()); }
    },

    select(id) {
      const s = this.s;
      if (!id || id === s.selected) return;
      s.selected = id;
      s.ctx.setPref('r3.selected', id);
      this.paintList(s.ctx.runs());
      this.paintJob();
    },

    // ------------------------------------------------------------------------------------------ the list ----
    deviceNames(runs) {
      // an entity's own name where a run is on the device itself ("fdcanusb · fixture"), else the device id
      const names = new Map();
      for (const run of runs) {
        if (names.has(run.device_id) && names.get(run.device_id).named) continue;
        const tail = ` · ${String(run.title || '').replace(/…$/, '')}`;
        const label = String(run.label || '');
        const entity = tail.length > 3 && label.endsWith(tail) ? label.slice(0, -tail.length) : label;
        if (run.path === run.device_path) names.set(run.device_id, { name: entity, named: true });
        else if (!names.has(run.device_id)) names.set(run.device_id, { name: run.device_id, named: false });
      }
      return names;
    },

    paintList(runs) {
      const s = this.s;
      const h = s.ctx.h;
      const names = this.deviceNames(runs);
      const devices = [...names.keys()].sort();
      if (s.device && !names.has(s.device)) s.device = '';
      const shown = s.device ? runs.filter((run) => run.device_id === s.device) : runs;
      const optionsHtml = `<option value="">All devices (${runs.length})</option>${devices.map((id) => `<option value="${h.esc(id)}"${id === s.device ? ' selected' : ''}>${h.esc(names.get(id).name)} · ${runs.filter((run) => run.device_id === id).length}</option>`).join('')}`;
      if (s.optionsHtml !== optionsHtml) { s.optionsHtml = optionsHtml; s.el.select.innerHTML = optionsHtml; s.el.select.value = s.device; }
      s.el.count.textContent = `${shown.length} run${shown.length === 1 ? '' : 's'}${runs.filter((run) => run.active).length ? ` · ${runs.filter((run) => run.active).length} going` : ''}`;
      const sig = shown.map((run) => `${run.id}|${run.state}|${run.stopping}|${run.id === s.selected}|${h.elapsed(run)}|${run.exit_code}`).join('\n');
      if (sig === s.listSig) return;
      s.listSig = sig;
      s.el.rows.innerHTML = shown.length ? shown.map((run) => this.rowHtml(run)).join('') : `<div class="r3-none">${runs.length ? 'No runs on this device.' : 'No runs yet. A utility you run from the Utilities menu is listed here.'}</div>`;
    },

    // the state's mark: a spinner while it goes, a check when it is done, a shape for what the page's shape rule allows
    mark(run) {
      const h = this.s.ctx.h;
      const words = h.stateWords(run);
      if (run.active) return `<span class="r3-spin${run.state === 'queued' || run.state === 'waiting' ? ' wait' : ''}" role="img" aria-label="${h.esc(words)}"></span>`;
      const kind = { failed: 'error', stopped: 'warn', timed_out: 'warn' }[run.state];
      return kind ? h.shape(kind, words) : tickSvg;
    },

    rowHtml(run) {
      const h = this.s.ctx.h;
      const when = (run.started_iso || '').slice(5, 19).replace('T', ' ');
      const ended = run.active ? '' : `${run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${run.exit_code}` : ''}`;
      return `<div class="r3-row" role="option" tabindex="-1" aria-selected="${run.id === this.s.selected}" data-r3-run="${h.esc(run.id)}" title="${h.esc(run.label)} · ${h.esc(h.stateWords(run))}">`
        + `<span class="r3-st">${this.mark(run)}</span><span class="r3-tx"><span class="r3-t">${h.esc(run.label)}</span>`
        + `<span class="r3-m">${h.esc(h.stateWords(run))} · ${h.esc(h.elapsed(run))}${h.esc(ended)} · ${h.esc(when)}</span></span></div>`;
    },

    // ------------------------------------------------------------------------------------------ the job ----
    dropDetail() {
      const s = this.s;
      if (s.detail && s.detail.bound) s.detail.bound.dispose();
      s.detail = null;
    },

    stepOpen(runId, step, run) {
      const s = this.s;
      const key = `${runId}:${step}`;
      if (s.steps.has(key)) return s.steps.get(key);
      // the live tail is open on the step that is going; the log is the point of the page, so it stays open; Exit opens once it is known
      if (step === 'pre') return false;
      if (step === 'run') return true;
      return !run.active;
    },

    toggleStep(key) {
      const s = this.s;
      const [step] = key.split(':');
      const run = s.ctx.run(s.selected);
      if (!run) return;
      s.steps.set(`${run.id}:${step}`, !this.stepOpen(run.id, step, run));
      this.paintJob();
      requestAnimationFrame(() => this.fitLog());
    },

    skeleton(run) {
      const s = this.s;
      const el = s.el.job;
      el.innerHTML = `<div class="r3-job-h"><h3></h3><span class="r3-pill"></span><span class="r3-sp"></span>`
        + `<div class="r3-btns"><button class="r3-btn" data-r3-stop hidden></button><button class="r3-btn" data-r3-again>Run again</button>`
        + `<a class="r3-btn" data-r3-cast download title="An asciinema recording: replay it with asciinema play">Download recording</a></div></div>`
        + `<dl class="r3-facts"></dl><p class="r3-note" hidden></p>`
        + `<div class="r3-steps">`
        + `<div class="r3-step" data-step="pre"><button class="r3-step-h" data-r3-step="pre:" aria-expanded="false">${chevSvg}<span class="r3-st" data-mark></span><span class="r3-step-n">Preconditions</span><span class="r3-step-s"></span><span class="r3-step-r"></span></button><div class="r3-step-b" hidden></div></div>`
        + `<div class="r3-step" data-step="run"><button class="r3-step-h" data-r3-step="run:" aria-expanded="true">${chevSvg}<span class="r3-st" data-mark></span><span class="r3-step-n">Run</span><span class="r3-step-s"></span><span class="r3-step-r"></span></button>`
        + `<div class="r3-step-b"><div class="r3-log"></div><div class="r3-grip" role="separator" aria-orientation="horizontal" aria-label="Resize the log" tabindex="0" title="Drag to resize · double-click to reset"></div></div></div>`
        + `<div class="r3-step" data-step="exit"><button class="r3-step-h" data-r3-step="exit:" aria-expanded="false">${chevSvg}<span class="r3-st" data-mark></span><span class="r3-step-n">Exit</span><span class="r3-step-s"></span><span class="r3-step-r"></span></button><div class="r3-step-b" hidden></div></div>`
        + `</div>`;
      const q = (selector) => el.querySelector(selector);
      const detail = { id: run.id, el, title: q('h3'), pill: q('.r3-pill'), stop: q('[data-r3-stop]'), again: q('[data-r3-again]'), cast: q('[data-r3-cast]'), facts: q('.r3-facts'), note: q('.r3-note'),
        steps: Object.fromEntries(['pre', 'run', 'exit'].map((name) => [name, { box: q(`[data-step="${name}"]`), head: q(`[data-step="${name}"] .r3-step-h`), mark: q(`[data-step="${name}"] [data-mark]`),
          sub: q(`[data-step="${name}"] .r3-step-s`), right: q(`[data-step="${name}"] .r3-step-r`), body: q(`[data-step="${name}"] .r3-step-b`) }])),
        log: q('.r3-log'), grip: q('.r3-grip'), bound: null, sig: {} };
      for (const name of ['pre', 'run', 'exit']) detail.steps[name].head.dataset.r3Step = `${name}:${run.id}`;
      // the log's bottom edge
      let drag = null;
      detail.grip.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        drag = { id: event.pointerId, y: event.clientY, h: s.logHeight };
        detail.grip.setPointerCapture(event.pointerId);
        detail.grip.classList.add('drag');
        document.body.classList.add('resizing');
        event.preventDefault();
      });
      detail.grip.addEventListener('pointermove', (event) => { if (drag && event.pointerId === drag.id) drag.next = this.applyLog(drag.h + (event.clientY - drag.y), false); });
      const end = (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        if (drag.next !== undefined) this.applyLog(drag.next, true);
        drag = null;
        detail.grip.classList.remove('drag');
        document.body.classList.remove('resizing');
        this.fitLog();
      };
      detail.grip.addEventListener('pointerup', end);
      detail.grip.addEventListener('pointercancel', end);
      detail.grip.addEventListener('dblclick', () => { this.applyLog(DEFAULT_LOG, 'reset'); this.fitLog(); });
      detail.grip.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        this.applyLog(s.logHeight + (event.key === 'ArrowDown' ? 16 : -16), true);
        this.fitLog();
        event.preventDefault();
      });
      return detail;
    },

    paintJob() {
      const s = this.s;
      const run = s.selected ? s.ctx.run(s.selected) : null;
      if (!run) {
        if (s.detail) this.dropDetail();
        if (!s.el.job.querySelector('.r3-none')) s.el.job.innerHTML = '<div class="r3-none">Pick a run to see its job.</div>';
        return;
      }
      const h = s.ctx.h;
      if (!s.detail || s.detail.id !== run.id) {
        this.dropDetail();
        s.detail = this.skeleton(run);
        if (!run.result) window.IORuns && window.IORuns.refresh && window.IORuns.refresh(run.id);
      }
      const d = s.detail;
      const set = (el, key, html) => { if (d.sig[key] !== html) { d.sig[key] = html; el.innerHTML = html; } };
      const text = (el, key, value) => { if (d.sig[key] !== value) { d.sig[key] = value; el.textContent = value; } };
      const words = h.stateWords(run);
      const bad = run.state === 'failed';
      const warn = run.state === 'stopped' || run.state === 'timed_out';
      text(d.title, 'title', `${h.plainTitle(run.title)} on ${this.entityOf(run)}`);
      d.pill.className = `r3-pill${bad ? ' err' : warn ? ' warn' : ''}`;
      set(d.pill, 'pill', `${run.active ? '<span class="r3-spin' + (run.state === 'queued' || run.state === 'waiting' ? ' wait' : '') + '"></span>' : (bad ? h.shape('error', words) : warn ? h.shape('warn', words) : '')}${h.esc(words)}${!run.active && run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${h.esc(run.exit_code)}` : ''}`);
      // Stop while it goes (the page may type into it, so only an interactive run), Run again, the recording
      const canStop = run.active && run.interactive;
      d.stop.hidden = !canStop;
      if (canStop) {
        const moves = run.danger === 'moves';
        const stopping = s.stopping.has(run.id) || run.stopping;
        d.stop.className = `r3-btn${moves ? ' moves' : ''}`;
        d.stop.disabled = stopping;
        d.stop.dataset.r3Stop = run.id;
        text(d.stop, 'stop', stopping ? 'Stopping…' : (moves ? 'Stop (motor goes limp)' : 'Stop'));
        d.stop.title = moves ? 'Interrupt it, then the plugin\'s own stop sends the board mode 0 (it goes limp)' : 'Interrupt it (SIGINT, then SIGTERM, then SIGKILL)';
      }
      d.again.dataset.r3Again = run.id;
      d.again.title = 'Dry run it again: the page shows what would run and waits for your confirm';
      d.cast.href = s.ctx.castUrl(run.id);
      d.cast.setAttribute('download', `${run.id}.cast`);
      // the facts
      const when = (iso) => (iso ? iso.slice(0, 19).replace('T', ' ') : '—');
      const facts = [['Device', run.device_id, true], ['Entity', run.path.split('/').slice(-2).join('/'), true], ['Plugin', run.plugin, false], ['Danger', run.danger, false],
        ['Started', when(run.started_iso), true], ['Ended', run.active ? '—' : when(run.ended_iso), true], ['Duration', h.elapsed(run), true],
        ['Exit code', run.exit_code === null || run.exit_code === undefined ? '—' : String(run.exit_code), true],
        ...(run.active ? [['Started from', run.interactive ? 'this host\'s page' : 'another process', false]] : []), ['Run', run.id, true]];
      set(d.facts, 'facts', facts.map(([name, value, mono]) => `<div class="r3-fact"><dt>${h.esc(name)}</dt><dd${mono ? ' class="mono"' : ''} title="${h.esc(value)}">${h.esc(value)}</dd></div>`).join(''));
      d.note.hidden = true;
      if (s.note && s.noteRun === run.id) { d.note.hidden = false; d.note.textContent = s.note; }
      this.paintSteps(d, run);
      // an ended run's result (records, the plugin's stop) comes with its stream's end; read it once if no terminal has
      if (!run.active && !run.result && !d.refreshing && window.IORuns && window.IORuns.refresh) { d.refreshing = true; window.IORuns.refresh(run.id); }
    },

    entityOf(run) {
      const tail = ` · ${String(run.title || '').replace(/…$/, '')}`;
      const label = String(run.label || '');
      return tail.length > 3 && label.endsWith(tail) ? label.slice(0, -tail.length) : label;
    },

    paintSteps(d, run) {
      const s = this.s;
      const h = s.ctx.h;
      const set = (el, key, html) => { if (d.sig[key] !== html) { d.sig[key] = html; el.innerHTML = html; } };
      const text = (el, key, value) => { if (d.sig[key] !== value) { d.sig[key] = value; el.textContent = value; } };
      const plan = this.planOf(run.id);
      const waiting = run.state === 'queued' || run.state === 'waiting';
      const kind = { failed: 'error', stopped: 'warn', timed_out: 'warn' }[run.state];
      const endMark = kind ? h.shape(kind, h.stateWords(run)) : tickSvg;
      // 1 · Preconditions: the gate the host passed before the process started
      const pre = d.steps.pre;
      set(pre.mark, 'pre-mark', waiting ? '<span class="r3-spin wait"></span>' : tickSvg);
      text(pre.sub, 'pre-sub', plan ? `${(plan.preconditions || []).length} precondition${(plan.preconditions || []).length === 1 ? '' : 's'} · the dry run you read` : 'no dry run kept for this run');
      text(pre.right, 'pre-right', waiting ? h.stateWords(run) : 'passed');
      set(pre.body, 'pre-body', plan ? this.planHtml(plan) : '<p class="r3-note">This run\'s dry run is not kept: it was started from another window, or before this browser kept plans. The command it ran is in the next step.</p>');
      // 2 · Run: the command and its terminal
      const step = d.steps.run;
      set(step.mark, 'run-mark', run.active ? `<span class="r3-spin${waiting ? ' wait' : ''}"></span>` : endMark);
      const command = (run.argv || []).join(' ');
      text(step.sub, 'run-sub', command);
      step.sub.title = command;
      text(step.right, 'run-right', `${run.active ? h.stateWords(run) : ''}${run.active ? ' ' : ''}${h.elapsed(run)}`.trim());
      // 3 · Exit
      const exit = d.steps.exit;
      const result = run.result || null;
      const plugin = run.stop && run.stop.plugin_stop;
      if (run.active) {
        set(exit.mark, 'exit-mark', dashSvg);
        text(exit.sub, 'exit-sub', run.stopping ? 'stopping' : 'waiting for the process to end');
        text(exit.right, 'exit-right', '');
        set(exit.body, 'exit-body', '<div class="r3-exit"><span>The process has not ended.</span></div>');
      } else {
        set(exit.mark, 'exit-mark', endMark);
        text(exit.sub, 'exit-sub', `${h.stateWords(run)}${run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${run.exit_code}` : ''}`);
        text(exit.right, 'exit-right', h.elapsed(run));
        const records = result && result.records ? result.records.map((record) => `<span class="mono">${h.esc(record.code || record.record || '')}</span> ${h.esc(record.message || record.name || '')}`) : [];
        set(exit.body, 'exit-body', `<div class="r3-exit"><span>${h.esc(h.stateWords(run))}${run.exit_code !== null && run.exit_code !== undefined ? ` with exit code <span class="mono">${h.esc(run.exit_code)}</span>` : ''} after ${h.esc(h.elapsed(run))}</span>`
          + `${result && result.message ? `<span class="${run.state === 'failed' ? 'bad' : ''}">${h.esc(result.message)}</span>` : ''}`
          + `${plugin && plugin.ran ? `<span>The plugin's stop ran: ${h.esc(plugin.message || plugin.outcome)}</span>` : (plugin && plugin.reason ? `<span>The plugin's stop did not run: ${h.esc(plugin.reason)}</span>` : '')}`
          + `${records.map((line) => `<span>${line}</span>`).join('')}</div>`);
      }
      // each step's fold; the Run log's terminal is made when its box is open
      for (const name of ['pre', 'run', 'exit']) {
        const open = this.stepOpen(run.id, name, run);
        const parts = d.steps[name];
        parts.head.setAttribute('aria-expanded', String(open));
        parts.body.hidden = !open;
      }
      // WHY no fit() here: the terminal's own ResizeObserver keeps it fitted; a fit() on every tick re-posts the size after the end
      if (this.stepOpen(run.id, 'run', run) && !d.bound) d.bound = s.ctx.terminal(d.log, run.id, { fontSize: 12 });
    },

    // the dry run's plan, kept per run in this browser
    keepPlan(util) {
      const s = this.s;
      const result = (util.dry && util.dry.result) || {};
      const answer = result.action_result || {};
      const plan = { command: Array.isArray(answer.command) ? answer.command.join(' ') : (answer.command || ''), confirm: answer.confirm || '',
        preconditions: (answer.preconditions || []).map((item) => ({ check: item.check, text: item.text, state: item.state })),
        steps: (answer.steps || []).map((step) => ({ purpose: step.purpose, runs: step.runs, argv: step.argv, sends: step.sends })) };
      s.plans.set(util.runId, plan);
      s.ctx.setPref(`r3.plan.${util.runId}`, JSON.stringify(plan));
      let index = [];
      try { index = JSON.parse(s.ctx.pref('r3.plans', '[]')); } catch (_) { index = []; }
      index.push(util.runId);
      while (index.length > PLAN_KEEP) s.ctx.setPref(`r3.plan.${index.shift()}`, '');
      s.ctx.setPref('r3.plans', JSON.stringify(index));
    },
    planOf(id) {
      const s = this.s;
      if (s.plans.has(id)) return s.plans.get(id);
      const raw = s.ctx.pref(`r3.plan.${id}`, '');
      if (!raw) return null;
      try { const plan = JSON.parse(raw); s.plans.set(id, plan); return plan; } catch (_) { return null; }
    },
    planHtml(plan) {
      const h = this.s.ctx.h;
      const rows = (plan.preconditions || []).map((item) => `<li><span class="st">·</span><span>${h.esc(item.text)}</span><span class="why">${h.esc(item.state || '')}</span></li>`).join('');
      const steps = (plan.steps || []).map((step) => `<li>${h.esc(step.purpose || '')}${step.argv && step.argv.length ? ` · <code>${h.esc(step.argv.join(' '))}</code>` : ''}</li>`).join('');
      return `${rows ? `<ul class="r3-pre">${rows}</ul>` : '<p class="r3-note">No preconditions.</p>'}`
        + `${plan.confirm ? `<p class="r3-note">Confirm text: ${h.esc(plan.confirm)}</p>` : ''}`
        + `${steps ? `<ol class="r3-plan">${steps}</ol>` : ''}`
        + `${plan.command ? `<p class="r3-note">It would run: <span class="r3-code">${h.esc(plan.command)}</span></p>` : ''}`;
    },

    // ------------------------------------------------------------------------------------------ actions ----
    async stop(id) {
      const s = this.s;
      s.stopping.add(id);
      this.paintJob();
      const answer = await s.ctx.interrupt(id);
      if (!answer.ok) {
        s.stopping.delete(id);
        s.note = `Stop was not sent: ${(answer.data && (answer.data.error || answer.data.refused)) || `HTTP ${answer.status}`}`;
        s.noteRun = id;
        this.paintJob();
      }
    },

    // Run again = the normal flow: take the page to the run's entity, open its Utilities menu and press the same utility, which
    // draws the dry-run sheet. Nothing runs until the person confirms there (and a new run opens this panel on itself).
    async runAgain(id) {
      const s = this.s;
      const run = s.ctx.run(id);
      if (!run) return;
      s.note = '';
      this.setOpen(false, false);
      const link = document.createElement('a');
      link.dataset.go = run.path;
      link.hidden = true;
      document.body.appendChild(link);
      link.click();
      link.remove();
      const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
      const deadline = Date.now() + 8000;
      let button = null;
      while (Date.now() < deadline) {
        await wait(100);
        button = document.querySelector('#ent-util-btn:not([disabled])');
        if (button) break;
      }
      const fail = (message) => { s.note = message; s.noteRun = id; this.setOpen(true, false); this.paintJob(); };
      if (!button) return fail('The page could not open this entity\'s Utilities (it may be gone): nothing was started.');
      // the utility is greyed while the page still counts the run (or its node reload) as busy: ask again until it is free
      let item = null;
      for (let i = 0; i < 100; i += 1) {
        if (!document.querySelector('.ent-menu')) { const toggle = document.querySelector('#ent-util-btn:not([disabled])'); if (toggle) toggle.click(); }
        item = document.querySelector(`.ent-menu [data-util="${run.action}"]`);
        if (item && !item.disabled) break;
        await wait(100);
      }
      if (!item) return fail(`${run.action} is not offered on this entity right now: nothing was started.`);
      if (item.disabled) return fail(`${run.action} is not available on this entity right now (${(item.closest('.urow') || item).textContent.trim().slice(0, 120)}): nothing was started.`);
      item.click();
    },
  };

  window.IORunViews = window.IORunViews || {};
  window.IORunViews[ID] = view;
}());
