// r4 · Runs in the Activity log (docs/runs-v1.md section 7; docs/activity-log-v1.md).
//
// A run is one row of the Activity log, "Run calibration on CAN 1 · running…", and its output streams in the Raw channel
// under that row: lines, not a terminal, with a reply box while the run waits for an answer and Stop on the row. Nothing
// else is a place for it: the entity's own log (the entity and what is below it, its kernel lines and its runs' output) opens
// at the bottom of the window when a run starts, and "Open in Activity" turns the same rows into the full view.
//
// How it fits the seam (it adds none): this view owns the live output. It follows each run it is asked to show through
// ctx.stream, turns the terminal's bytes into lines (activity/core.js createLineAssembler: escape sequences dropped, a \r
// progress bar is one line), and hands the lines to the log through IOActivityLog.attachRuns. The rows, their folding and the
// filters are the log's; ctx.root holds an embedded log scoped to the entity; open(id) reveals a run in it (or in the Activity
// view itself when that is what is on screen).
//
// WHY lines and not xterm.js: Zach asked to see a tool's output and to answer its prompt (2026-10-24), and the Activity log
// is where his other events already are (2026-10-24: "a single stream you can read to get an entire picture of the device").
// A full-screen program (vi, less) is r1's job; a tool that prints and asks a question is this one's.
'use strict';
(function () {
  const ID = 'r4';
  const DEFAULT_HEIGHT = 300;
  const MIN_HEIGHT = 140;
  const CSS = `
#runs-root.r4 { position: relative; flex: none; display: flex; flex-direction: column; height: var(--r4-h, ${DEFAULT_HEIGHT}px); border-top: 1px solid var(--io-border); background: var(--io-surface); min-height: 0; }
#runs-root.r4[hidden] { display: none; }
.r4-sash { position: absolute; left: 0; right: 0; top: -4px; height: 8px; z-index: 6; cursor: row-resize; touch-action: none; outline: none; }
.r4-sash::before { content: ""; position: absolute; left: 0; right: 0; top: 3px; height: 1px; background: transparent; transition: background .1s, height .1s, top .1s; }
.r4-sash:hover::before, .r4-sash:focus-visible::before, body.resizing .r4-sash::before { top: 2px; height: 3px; background: var(--io-selected-line); }
.r4-bar { flex: none; display: flex; align-items: center; gap: 10px; min-height: 32px; padding: 0 8px 0 14px; background: var(--io-surface-2); border-bottom: 1px solid var(--io-border-soft); font-size: 12px; }
.r4-title { font-weight: 600; color: var(--io-text-strong); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.r4-meta { flex: 1; min-width: 0; color: var(--io-muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.r4-btn { border: 0; background: transparent; color: var(--io-accent-text); font: inherit; font-size: 12px; cursor: pointer; padding: 2px 6px; border-radius: var(--io-radius-sm); }
.r4-btn:hover { background: var(--io-hover); }
.r4-hide { flex: none; width: 22px; height: 22px; display: inline-flex; align-items: center; justify-content: center; border: 0; background: transparent; color: var(--io-faint); border-radius: var(--io-radius-sm); cursor: pointer; padding: 0; font-size: 14px; line-height: 1; }
.r4-hide:hover { background: var(--io-hover); color: var(--io-text); }
.r4-body { flex: 1; min-height: 0; position: relative; }
.r4-log { position: absolute; inset: 0; }
.r4-body .al-bar { padding-top: 6px; padding-bottom: 6px; }
.r4-toggle { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: var(--io-muted); font: inherit; font-size: 12px; padding: 2px 7px; border-radius: var(--io-radius-sm); cursor: pointer; }
.r4-toggle:hover { background: var(--io-hover); color: var(--io-text); }
.r4-toggle[aria-pressed="true"] { color: var(--io-text-strong); }
.r4-toggle .ic { width: 13px; height: 13px; }
.r4-badge { min-width: 15px; height: 15px; padding: 0 4px; border-radius: 8px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10.5px; font-weight: 600; line-height: 15px; text-align: center; }
.r4-badge[hidden] { display: none; }
`;

  // The provider the log draws a run's output from (IOActivityLog.attachRuns): the log's own, on this context's runtime.
  const makeProvider = (ctx) => window.IOActivityLog.runsProvider({ stream: ctx.stream, get: ctx.run, elapsed: ctx.h.elapsed, sendInput: ctx.sendInput, interrupt: ctx.interrupt });

  const view = {
    name: 'Runs in the Activity log',
    description: 'A run is one row of the Activity log and its output streams under it; the entity\'s own log opens at the bottom while it goes',
    order: 4,

    mount(ctx) {
      const s = { ctx, provider: makeProvider(ctx), open: ctx.pref('r4.open', '0') === '1', runId: null, path: '', log: null, height: Number(ctx.pref('r4.height', String(DEFAULT_HEIGHT))) || DEFAULT_HEIGHT,
        wasActive: new Set(), off: [], timer: null };
      this.s = s;
      ctx.css('r4', CSS);
      if (window.IOActivityLog) window.IOActivityLog.attachRuns(s.provider);
      const root = ctx.root;
      root.classList.add('r4');
      root.innerHTML = `<div class="r4-sash" role="separator" aria-orientation="horizontal" aria-label="Resize the log panel" tabindex="0" title="Drag to resize · double-click to reset"></div>`
        + `<div class="r4-bar"><span class="r4-title">Activity</span><span class="r4-meta"></span>`
        + `<button class="r4-btn" data-r4-full title="The same rows in the Activity view">Open in Activity</button>`
        + `<button class="r4-hide" data-r4-hide title="Hide the log" aria-label="Hide the log">×</button></div><div class="r4-body"></div>`;
      s.el = { sash: root.querySelector('.r4-sash'), title: root.querySelector('.r4-title'), meta: root.querySelector('.r4-meta'), body: root.querySelector('.r4-body') };
      ctx.statusSlot.innerHTML = `<button class="r4-toggle" aria-pressed="false" aria-expanded="false" aria-controls="runs-root" title="The log of the entity a run is on">`
        + `<svg class="ic" aria-hidden="true"><use href="#i-stream"/></svg><span>Run log</span><span class="r4-badge" hidden></span></button>`;
      s.el.toggle = ctx.statusSlot.querySelector('.r4-toggle');
      s.el.badge = s.el.toggle.querySelector('.r4-badge');
      this.applyHeight(s.height, false);
      this.wire();
      s.off.push(ctx.onChange(() => this.update(ctx)));
      s.timer = setInterval(() => this.update(ctx), 1000);
      // a reload during a run finds it again: the newest active run is the one the log is about
      const going = ctx.runs().find((run) => run.active);
      if (going && s.open) this.show(going);
      this.update(ctx);
    },

    unmount(ctx) {
      const s = this.s;
      if (!s) return;
      clearInterval(s.timer);
      s.off.forEach((off) => off());
      if (s.log) s.log.unmount();
      if (window.IOActivityLog) window.IOActivityLog.detachRuns(s.provider);
      s.provider.dispose();
      ctx.root.removeEventListener('click', s.click);
      window.removeEventListener('resize', s.onResize);
      document.body.classList.remove('resizing');
      ctx.root.classList.remove('r4');
      ctx.root.style.removeProperty('--r4-h');
      ctx.root.innerHTML = '';
      ctx.root.hidden = true;
      ctx.statusSlot.innerHTML = '';
      this.s = null;
    },

    wire() {
      const s = this.s;
      const root = s.ctx.root;
      root.addEventListener('click', s.click = (event) => {
        if (event.target.closest('[data-r4-hide]')) this.setOpen(false);
        if (event.target.closest('[data-r4-full]')) this.openFull();
      });
      s.el.toggle.addEventListener('click', () => {
        if (!s.open && !s.runId) {
          const run = s.ctx.runs()[0];
          if (run) this.show(run);
        }
        this.setOpen(!s.open);
      });
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
      };
      s.el.sash.addEventListener('pointerup', end);
      s.el.sash.addEventListener('pointercancel', end);
      s.el.sash.addEventListener('dblclick', () => { s.ctx.setPref('r4.height', ''); this.applyHeight(DEFAULT_HEIGHT, false); });
      s.el.sash.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        this.applyHeight(s.height + (event.key === 'ArrowUp' ? 16 : -16), true);
        event.preventDefault();
      });
      window.addEventListener('resize', s.onResize = () => this.applyHeight(s.height, false));
    },

    applyHeight(height, persist) {
      const s = this.s;
      const value = Math.round(Math.max(MIN_HEIGHT, Math.min(height, Math.max(MIN_HEIGHT, window.innerHeight - 220))));
      s.height = value;
      s.ctx.root.style.setProperty('--r4-h', `${value}px`);
      if (persist) s.ctx.setPref('r4.height', String(value));
      return value;
    },

    setOpen(open) {
      const s = this.s;
      s.open = open;
      s.ctx.setPref('r4.open', open ? '1' : '0');
      s.ctx.root.hidden = !open;
      s.el.toggle.setAttribute('aria-pressed', String(open));
      s.el.toggle.setAttribute('aria-expanded', String(open));
    },

    // The log at the bottom is about one run's entity. A different entity moves its scope; the run's row is open in it.
    show(run) {
      const s = this.s;
      s.runId = run.id;
      const name = String(run.label || '').split(' · ')[0] || run.path;
      if (!s.log) {
        s.log = window.IOActivityLog.create({ compact: true, scope: { path: run.path, name, mode: 'children' }, raw: true, fixedScope: true, floor: 'info' });
        s.log.mount(s.el.body.appendChild(Object.assign(document.createElement('div'), { className: 'r4-log' })));
        s.path = run.path;
      } else if (s.path !== run.path) {
        s.path = run.path;
        s.log.setScope({ path: run.path, name, mode: 'children' });
      } else if (run.active) {
        s.log.resume(); // a new run on the same entity: the log was paused on the last one's output
        s.log.reload();
      }
      if (run.active) s.wasActive.add(run.id); else s.log.reveal(run.id, run);
      this.setOpen(true);
    },

    // The Utilities run line's "Show output", and a run that has just started: show it where it is on screen. In the
    // Activity view that is the main log (its row opens); anywhere else the entity's log opens at the bottom.
    open(id, ctx) {
      const s = this.s;
      const run = ctx.run(id);
      if (!s || !run) return;
      const host = document.getElementById('activity-host');
      if (host && !host.hidden && window.IOActivityLog) {
        window.IOActivityLog.main(host).reveal(id, run);
        return;
      }
      this.show(run);
      this.update(ctx);
    },

    openFull() {
      const s = this.s;
      const run = s.runId && s.ctx.run(s.runId);
      const tab = document.querySelector('[data-view="activity"]');
      if (tab) tab.click();
      if (run) setTimeout(() => { const host = document.getElementById('activity-host'); if (host && window.IOActivityLog) window.IOActivityLog.main(host).reveal(run.id, run); }, 0);
    },

    update(ctx) {
      const s = this.s;
      if (!s) return;
      const runs = ctx.runs();
      const going = runs.filter((run) => run.active).length;
      s.el.badge.hidden = !going;
      s.el.badge.textContent = String(going);
      s.el.toggle.title = going ? `Run log: ${going} run${going === 1 ? '' : 's'} going` : 'The log of the entity a run is on';
      s.ctx.root.hidden = !s.open;
      s.el.toggle.setAttribute('aria-pressed', String(s.open));
      s.el.toggle.setAttribute('aria-expanded', String(s.open));
      // a reload with the log open: it is about the run that is going, else the latest one
      if (s.open && !s.runId && runs.length) this.show(runs.find((run) => run.active) || runs[0]);
      const run = s.runId ? ctx.run(s.runId) : null;
      if (run) {
        const words = ctx.h.stateWords(run);
        const name = String(run.label || '').split(' · ')[0] || run.path;
        s.el.title.textContent = `Log of ${name}`;
        const text = `${String(run.label || '').split(' · ')[1] || 'run'} · ${words}${run.exit_code !== null && run.exit_code !== undefined && !run.active ? ` · exit ${run.exit_code}` : ''} · ${ctx.h.elapsed(run)}`;
        if (s.el.meta.textContent !== text) s.el.meta.textContent = text;
        // the run has just ended: its row stays open, so the last lines and how it ended are still there
        if (!run.active && s.wasActive.has(run.id) && s.log) { s.wasActive.delete(run.id); s.log.reveal(run.id, run); }
      }
    },

    // the Utilities run line is the default one ("Show output" calls open above)
    runLine() { return null; },
  };

  window.IORunViews = window.IORunViews || {};
  window.IORunViews[ID] = view;
}());
