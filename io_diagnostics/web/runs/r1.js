// r1 · Terminal panel with tabs (the default run view; docs/runs-v1.md section 7).
//
// A bottom panel like the Claude desktop app's terminal pane or VS Code's: one tab per run, titled "CAN 1 · Run
// calibration", with its status shape, an elapsed timer and, when it is over, how it ended. The selected tab is a live
// xterm.js terminal (typing goes to the process while it runs; a finished tab is a scrollback). A Stop button sits on the
// tab of every running run ("Stop (motor goes limp)" for a moves run). Finished tabs stay until closed. The panel's
// height drags (remembered, double-click resets, arrow keys move it); a status-bar toggle opens it and badges the number
// of runs going; the Utilities run line's "Show output" opens the run's tab.
//
// WHY a panel and not a window: Zach, 2026-10-24: "I think the claude model is good like you run this and then there is
// like all the terminals process you can switch between." WHY the tabs are for runs seen on THIS page (running ones, ones
// opened with Show output, ones picked from "Earlier runs"): the host keeps the last runs on disk, and thirty old tabs on
// every reload would be noise.
'use strict';
(function () {
  const ID = 'r1';
  const DEFAULT_HEIGHT = 280;
  const MIN_HEIGHT = 120;
  const CSS = `
#runs-root.r1 { position: relative; flex: none; display: flex; flex-direction: column; height: var(--r1-h, ${DEFAULT_HEIGHT}px); border-top: 1px solid var(--io-border); background: var(--io-surface); min-height: 0; }
#runs-root.r1[hidden] { display: none; }
.r1-sash { position: absolute; left: 0; right: 0; top: -4px; height: 8px; z-index: 5; cursor: row-resize; touch-action: none; outline: none; }
.r1-sash::before { content: ""; position: absolute; left: 0; right: 0; top: 3px; height: 1px; background: transparent; transition: background .1s, height .1s, top .1s; }
.r1-sash:hover::before, .r1-sash:focus-visible::before, body.resizing .r1-sash::before { top: 2px; height: 3px; background: var(--io-selected-line); }
.r1-bar { flex: none; display: flex; align-items: stretch; min-height: 34px; background: var(--io-surface-2); border-bottom: 1px solid var(--io-border-soft); }
.r1-tabs { flex: 1; min-width: 0; display: flex; overflow-x: auto; scrollbar-width: thin; }
.r1-tab { flex: none; display: flex; align-items: center; gap: 7px; padding: 0 6px 0 12px; border-right: 1px solid var(--io-border-soft); border-bottom: 2px solid transparent; margin-bottom: -1px; color: var(--io-muted); font-size: 12px; cursor: pointer; white-space: nowrap; outline: none; }
.r1-tab:hover { background: var(--io-hover); color: var(--io-text); }
.r1-tab:focus-visible { box-shadow: inset 0 0 0 2px var(--io-focus); }
.r1-tab[aria-selected="true"] { background: var(--io-surface); color: var(--io-text-strong); border-bottom-color: var(--io-selected-line); }
.r1-title { font-weight: 500; }
.r1-meta { color: var(--io-faint); font-family: var(--io-font-mono); font-size: 11.5px; }
.r1-shape { width: 13px; height: 13px; flex: none; display: inline-flex; align-items: center; justify-content: center; }
.r1-shape .shape { width: 13px; height: 13px; }
.r1-shape.lv-warn { color: var(--io-warn); } .r1-shape.lv-error { color: var(--io-error); } .r1-shape.lv-stale { color: var(--io-stale); }
.r1-spin { width: 9px; height: 9px; border-radius: 50%; border: 2px solid var(--io-border); border-top-color: var(--io-text); animation: r1-spin 0.9s linear infinite; }
.r1-spin.wait { animation: none; border-top-color: var(--io-border); border-style: dotted; }
@keyframes r1-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .r1-spin { animation: none; } }
.r1-stop { flex: none; border: 1px solid var(--io-border); background: var(--io-field); color: var(--io-text); border-radius: var(--io-radius-sm); padding: 2px 9px; font: inherit; font-size: 11.5px; cursor: pointer; }
.r1-stop:hover:not([disabled]) { background: var(--io-hover); }
.r1-stop.moves { background: var(--io-text-strong); border-color: var(--io-text-strong); color: var(--io-surface); }
.r1-stop.moves:hover:not([disabled]) { background: var(--io-text); }
.r1-stop[disabled] { opacity: .55; cursor: default; }
.r1-close, .r1-hide { flex: none; width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; border: 0; background: transparent; color: var(--io-faint); border-radius: var(--io-radius-sm); cursor: pointer; padding: 0; font: inherit; font-size: 14px; line-height: 1; }
.r1-close:hover, .r1-hide:hover { background: var(--io-hover); color: var(--io-text); }
.r1-tools { flex: none; display: flex; align-items: center; gap: 4px; padding: 0 6px; }
.r1-earlier { appearance: none; -webkit-appearance: none; background: transparent; color: var(--io-muted); border: 0; border-radius: var(--io-radius-sm); padding: 3px 6px; font: inherit; font-size: 12px; cursor: pointer; max-width: 180px; }
.r1-earlier:hover { background: var(--io-hover); color: var(--io-text); }
.r1-earlier option { background: var(--io-popover); color: var(--io-text); }
.r1-body { position: relative; flex: 1; min-height: 0; background: var(--io-surface); }
.r1-pane { position: absolute; inset: 0; padding: 6px 4px 2px 10px; }
.r1-pane[hidden] { display: none; }
.r1-pane .xterm { height: 100%; }
.r1-pane .xterm-viewport { background: transparent !important; }
.r1-empty { padding: 18px 14px; color: var(--io-faint); font-size: 12.5px; }
.r1-foot { flex: none; display: flex; gap: 10px; align-items: baseline; padding: 3px 12px; border-top: 1px solid var(--io-border-soft); background: var(--io-surface-2); color: var(--io-faint); font-size: 11.5px; min-height: 22px; }
.r1-foot a { color: var(--io-accent-text); }
.r1-foot .r1-fail { color: var(--io-error-text); }
.r1-toggle { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: var(--io-muted); font: inherit; font-size: 12px; padding: 2px 7px; border-radius: var(--io-radius-sm); cursor: pointer; }
.r1-toggle:hover { background: var(--io-hover); color: var(--io-text); }
.r1-toggle[aria-pressed="true"] { color: var(--io-text-strong); }
.r1-toggle .ic { width: 13px; height: 13px; }
.r1-badge { min-width: 15px; height: 15px; padding: 0 4px; border-radius: 8px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10.5px; font-weight: 600; line-height: 15px; text-align: center; }
.r1-badge[hidden] { display: none; }
`;

  const view = {
    name: 'Terminal panel with tabs',
    description: 'A bottom panel with one tab per run: a live terminal you can type into, a Stop button on the tab, finished tabs kept until closed',
    order: 1,
    mount(ctx) {
      const s = { ctx, tabs: new Map(), terminals: new Map(), selected: null, closed: new Set(), stopping: new Set(), open: ctx.pref('r1.open', '0') === '1',
        height: Number(ctx.pref('r1.height', String(DEFAULT_HEIGHT))) || DEFAULT_HEIGHT, off: [], timer: null, seen: new Set() };
      this.s = s;
      ctx.css('r1', CSS);
      const root = ctx.root;
      root.classList.add('r1');
      root.innerHTML = `<div class="r1-sash" role="separator" aria-orientation="horizontal" aria-label="Resize the terminal panel" tabindex="0" title="Drag to resize · double-click to reset"></div>`
        + `<div class="r1-bar"><div class="r1-tabs" role="tablist" aria-label="Runs"></div><div class="r1-tools">`
        + `<select class="r1-earlier" aria-label="Earlier runs" title="Earlier runs (kept on disk)"></select>`
        + `<button class="r1-hide" title="Hide the panel (Ctrl+\`)" aria-label="Hide the panel">${chevron()}</button></div></div>`
        + `<div class="r1-body"><div class="r1-empty">No runs yet. A utility you run from the Utilities menu shows its terminal here.</div></div>`
        + `<div class="r1-foot" role="status"></div>`;
      s.el = { sash: root.querySelector('.r1-sash'), tabs: root.querySelector('.r1-tabs'), body: root.querySelector('.r1-body'), foot: root.querySelector('.r1-foot'),
        earlier: root.querySelector('.r1-earlier'), hide: root.querySelector('.r1-hide') };
      ctx.statusSlot.innerHTML = `<button class="r1-toggle" aria-pressed="false" aria-expanded="false" aria-controls="runs-root" title="Terminal panel (Ctrl+\`)">`
        + `<svg class="ic" aria-hidden="true"><use href="#i-tty"/></svg><span>Terminal</span><span class="r1-badge" hidden></span></button>`;
      s.el.toggle = ctx.statusSlot.querySelector('.r1-toggle');
      s.el.badge = s.el.toggle.querySelector('.r1-badge');
      this.applyHeight(s.height, false);
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
      for (const bound of s.terminals.values()) bound.dispose();
      document.removeEventListener('keydown', s.key, true);
      window.removeEventListener('resize', s.onResize);
      ctx.root.removeEventListener('click', s.click);
      document.body.classList.remove('resizing');
      ctx.root.classList.remove('r1');
      ctx.root.style.removeProperty('--r1-h');
      ctx.root.innerHTML = '';
      ctx.root.hidden = true;
      ctx.statusSlot.innerHTML = '';
      this.s = null;
    },

    // ---------------------------------------------------------------------------------------------- wiring ----
    wire() {
      const s = this.s;
      const { ctx } = s;
      const root = ctx.root;
      root.addEventListener('click', s.click = (event) => {
        const stop = event.target.closest('[data-r1-stop]');
        if (stop) { this.stop(stop.dataset.r1Stop); event.stopPropagation(); return; }
        const close = event.target.closest('[data-r1-close]');
        if (close) { this.close(close.dataset.r1Close); event.stopPropagation(); return; }
        const tab = event.target.closest('[data-r1-tab]');
        if (tab) this.select(tab.dataset.r1Tab, true);
        if (event.target.closest('.r1-hide')) this.setOpen(false);
      });
      s.el.tabs.addEventListener('keydown', (event) => {
        const tab = event.target.closest('[data-r1-tab]');
        if (!tab) return;
        const ids = [...s.tabs.keys()];
        const at = ids.indexOf(tab.dataset.r1Tab);
        if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
          const next = ids[(at + (event.key === 'ArrowRight' ? 1 : ids.length - 1)) % ids.length];
          this.select(next, false);
          s.tabs.get(next).el.focus();
          event.preventDefault();
        } else if (event.key === 'Enter' || event.key === ' ') {
          this.select(tab.dataset.r1Tab, true);
          event.preventDefault();
        } else if ((event.key === 'Delete' || event.key === 'Backspace') && !(ctx.run(tab.dataset.r1Tab) || {}).active) {
          this.close(tab.dataset.r1Tab);
          event.preventDefault();
        }
      });
      s.el.earlier.addEventListener('change', () => {
        const id = s.el.earlier.value;
        s.el.earlier.value = '';
        if (id) this.open(id, ctx);
      });
      s.el.toggle.addEventListener('click', () => this.setOpen(!s.open));
      document.addEventListener('keydown', s.key = (event) => {
        if (event.ctrlKey && event.key === '`' && !event.altKey && !event.metaKey) { this.setOpen(!s.open, true); event.preventDefault(); }
      }, true);
      // the boundary drags (Zach's standing rule), is remembered, resets on double-click and moves by arrow keys
      let drag = null;
      s.el.sash.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        drag = { id: event.pointerId };
        s.el.sash.setPointerCapture(event.pointerId);
        document.body.classList.add('resizing');
        event.preventDefault();
      });
      s.el.sash.addEventListener('pointermove', (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        drag.height = this.applyHeight(root.getBoundingClientRect().bottom - event.clientY, false);
      });
      const end = (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        if (drag.height !== undefined) this.applyHeight(drag.height, true);
        drag = null;
        document.body.classList.remove('resizing');
        this.fitAll();
      };
      s.el.sash.addEventListener('pointerup', end);
      s.el.sash.addEventListener('pointercancel', end);
      s.el.sash.addEventListener('dblclick', () => { ctx.setPref('r1.height', ''); this.applyHeight(DEFAULT_HEIGHT, false); this.fitAll(); });
      s.el.sash.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        this.applyHeight(s.height + (event.key === 'ArrowUp' ? 16 : -16), true);
        this.fitAll();
        event.preventDefault();
      });
      window.addEventListener('resize', s.onResize = () => { this.applyHeight(s.height, false); this.fitAll(); });
    },

    applyHeight(height, persist) {
      const s = this.s;
      const max = Math.max(MIN_HEIGHT, window.innerHeight - 220);
      const value = Math.round(Math.max(MIN_HEIGHT, Math.min(height, max)));
      s.height = value;
      s.ctx.root.style.setProperty('--r1-h', `${value}px`);
      if (persist) s.ctx.setPref('r1.height', String(value));
      return value;
    },
    fitAll() { for (const bound of this.s.terminals.values()) bound.fit(); },

    // ---------------------------------------------------------------------------------------------- state ----
    setOpen(open, focus) {
      const s = this.s;
      s.open = open;
      s.ctx.setPref('r1.open', open ? '1' : '0');
      s.ctx.root.hidden = !open;
      s.el.toggle.setAttribute('aria-pressed', String(open));
      s.el.toggle.setAttribute('aria-expanded', String(open));
      if (open) {
        this.select(s.selected || [...s.tabs.keys()][0] || null, Boolean(focus));
        requestAnimationFrame(() => this.fitAll());
      }
    },

    // Show a run: its tab exists, is selected, the panel is open and (asked to) the terminal has the keyboard.
    open(id, ctx) {
      const s = this.s;
      if (!s || !ctx.run(id)) return;
      s.closed.delete(id);
      this.ensureTab(ctx.run(id));
      this.update(ctx);
      this.setOpen(true, false);
      this.select(id, true);
    },

    ensureTab(run) {
      const s = this.s;
      if (s.tabs.has(run.id)) return s.tabs.get(run.id);
      const el = document.createElement('div');
      el.className = 'r1-tab';
      el.setAttribute('role', 'tab');
      el.tabIndex = -1;
      el.dataset.r1Tab = run.id;
      el.innerHTML = `<span class="r1-shape"></span><span class="r1-title"></span><span class="r1-meta"></span>`
        + `<button class="r1-stop" data-r1-stop="${run.id}"></button><button class="r1-close" data-r1-close="${run.id}" aria-label="Close this tab" title="Close this tab">${closeIcon()}</button>`;
      s.el.tabs.appendChild(el);
      const pane = document.createElement('div');
      pane.className = 'r1-pane';
      pane.dataset.r1Pane = run.id;
      pane.hidden = true;
      s.el.body.appendChild(pane);
      const tab = { el, pane, id: run.id, shapeEl: el.querySelector('.r1-shape'), titleEl: el.querySelector('.r1-title'), metaEl: el.querySelector('.r1-meta'),
        stopEl: el.querySelector('.r1-stop'), closeEl: el.querySelector('.r1-close') };
      s.tabs.set(run.id, tab);
      const empty = s.el.body.querySelector('.r1-empty');
      if (empty) empty.hidden = true;
      return tab;
    },

    select(id, focus) {
      const s = this.s;
      if (!id || !s.tabs.has(id)) { s.selected = null; this.paint(); return; }
      s.selected = id;
      for (const [key, tab] of s.tabs) {
        const on = key === id;
        tab.el.setAttribute('aria-selected', String(on));
        tab.el.tabIndex = on ? 0 : -1;
        tab.pane.hidden = !on;
      }
      if (s.open && !s.terminals.has(id)) s.terminals.set(id, s.ctx.terminal(s.tabs.get(id).pane, id));
      requestAnimationFrame(() => {
        const bound = s.terminals.get(id);
        if (bound) { bound.fit(); if (focus) bound.focus(); }
      });
      this.paint();
    },

    close(id) {
      const s = this.s;
      const run = s.ctx.run(id);
      if (run && run.active) return; // a running tab is closed when it has ended
      const tab = s.tabs.get(id);
      if (!tab) return;
      const bound = s.terminals.get(id);
      if (bound) { bound.dispose(); s.terminals.delete(id); }
      tab.el.remove();
      tab.pane.remove();
      s.tabs.delete(id);
      s.closed.add(id);
      if (s.selected === id) this.select([...s.tabs.keys()].pop() || null, false);
      this.update(s.ctx);
    },

    async stop(id) {
      const s = this.s;
      s.stopping.add(id);
      this.update(s.ctx);
      const answer = await s.ctx.interrupt(id);
      if (!answer.ok) {
        s.stopping.delete(id);
        s.el.foot.textContent = `Stop was not sent: ${(answer.data && (answer.data.error || answer.data.refused)) || `HTTP ${answer.status}`}`;
      }
    },

    // ---------------------------------------------------------------------------------------------- drawing ----
    update(ctx) {
      const s = this.s;
      if (!s) return;
      const runs = ctx.runs();
      // a run going now has a tab (a reload during a calibration finds it again); a finished one only if it had one
      for (const run of runs) {
        if (run.active && !s.closed.has(run.id)) {
          const fresh = !s.tabs.has(run.id);
          this.ensureTab(run);
          if (fresh && !s.selected) this.select(run.id, false);
        }
        if (!run.active) s.stopping.delete(run.id);
      }
      for (const tab of s.tabs.values()) this.paintTab(tab, ctx.run(tab.id), ctx);
      // earlier runs: those on disk with no tab, newest first
      const earlier = runs.filter((run) => !s.tabs.has(run.id) && !run.active);
      const signature = earlier.map((run) => `${run.id}${run.state}`).join();
      if (signature !== s.earlierSig) {
        s.earlierSig = signature;
        s.el.earlier.innerHTML = `<option value="">Earlier runs${earlier.length ? ` (${earlier.length})` : ''}</option>`
          + earlier.map((run) => `<option value="${ctx.h.esc(run.id)}">${ctx.h.esc(`${run.label} · ${ctx.h.stateWords(run)} · ${(run.started_iso || '').slice(5, 16).replace('T', ' ')}`)}</option>`).join('');
        s.el.earlier.hidden = !earlier.length;
      }
      const going = runs.filter((run) => run.active).length;
      s.el.badge.hidden = !going;
      s.el.badge.textContent = String(going);
      s.el.toggle.title = going ? `Terminal panel: ${going} run${going === 1 ? '' : 's'} going (Ctrl+\`)` : 'Terminal panel (Ctrl+`)';
      s.ctx.root.hidden = !s.open;
      s.el.toggle.setAttribute('aria-pressed', String(s.open));
      s.el.toggle.setAttribute('aria-expanded', String(s.open));
      if (s.open && !s.selected && s.tabs.size) this.select([...s.tabs.keys()][0], false);
      this.paint();
    },

    paintTab(tab, run, ctx) {
      const s = this.s;
      if (!run) return;
      const h = ctx.h;
      const words = h.stateWords(run);
      const shapeKind = { failed: 'error', stopped: 'warn', timed_out: 'stale' }[run.state];
      const shape = run.active ? `<span class="r1-spin${run.state === 'queued' || run.state === 'waiting' ? ' wait' : ''}" role="img" aria-label="${h.esc(words)}"></span>`
        : shapeKind ? h.shape(shapeKind, words) : '';
      const sig = `${run.state}|${run.stopping}`;
      if (tab.sig !== sig) {
        tab.sig = sig;
        tab.shapeEl.className = `r1-shape${shapeKind ? ` lv-${shapeKind}` : ''}`;
        tab.shapeEl.innerHTML = shape;
      }
      tab.titleEl.textContent = run.label;
      tab.el.title = `${run.label} · ${words}${run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${run.exit_code}` : ''}`;
      const elapsed = h.elapsed(run);
      tab.metaEl.textContent = run.active ? (run.state === 'queued' || run.state === 'waiting' ? `${words} · ${elapsed}` : elapsed)
        : `${words}${run.state === 'failed' && run.exit_code ? ` · exit ${run.exit_code}` : ''} · ${elapsed}`;
      const canStop = run.active && run.interactive;
      tab.stopEl.hidden = !canStop;
      if (canStop) {
        const moves = run.danger === 'moves';
        const stopping = s.stopping.has(run.id) || run.stopping;
        tab.stopEl.className = `r1-stop${moves ? ' moves' : ''}`;
        tab.stopEl.disabled = stopping;
        tab.stopEl.textContent = stopping ? 'Stopping…' : (moves ? 'Stop (motor goes limp)' : 'Stop');
        tab.stopEl.title = moves ? 'Interrupt it, then the plugin\'s own stop sends the board mode 0 (it goes limp)' : 'Interrupt it (SIGINT, then SIGTERM, then SIGKILL)';
      }
      tab.closeEl.hidden = run.active;
    },

    paint() {
      const s = this.s;
      const run = s.selected ? s.ctx.run(s.selected) : null;
      const h = s.ctx.h;
      s.el.body.querySelector('.r1-empty').hidden = s.tabs.size > 0;
      let text = '';
      if (!run) text = s.tabs.size ? '' : 'Typing goes to the process while a run is going.';
      else if (run.active && run.interactive) text = 'Typing goes to the process · Ctrl-C stops it';
      else if (run.active) text = 'Started from another window of this host: you can watch it here, not type into it';
      else {
        const link = `<a href="${s.ctx.castUrl(run.id)}" download="${h.esc(run.id)}.cast" title="An asciinema recording: replay it with asciinema play">recording (.cast)</a>`;
        const fail = run.state === 'failed' || run.state === 'timed_out' ? 'r1-fail' : '';
        text = `<span class="${fail}">${h.esc(h.stateWords(run))}${run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${run.exit_code}` : ''} · ${h.esc(h.elapsed(run))}</span>`
          + `${run.stop && run.stop.plugin_stop && run.stop.plugin_stop.ran ? `<span>the plugin\'s stop: ${h.esc(run.stop.plugin_stop.message || run.stop.plugin_stop.outcome)}</span>` : ''}${link}`;
      }
      if (s.footHtml !== text) { s.footHtml = text; s.el.foot.innerHTML = text; }
    },
  };

  function chevron() { return '<svg class="ic" viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>'; }
  function closeIcon() { return '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>'; }

  window.IORunViews = window.IORunViews || {};
  window.IORunViews[ID] = view;
}());
