// r2 · Under the action (docs/runs-v1.md section 7).
//
// The output lives where the action was started: the Utilities run line under the entity's header expands in place into
// the live terminal, with an inline answer box and Stop beside it. A moves run says "Stop (motor goes limp)". "Pop out"
// opens the run in its own browser window (runs/r2-popout.html, served by this page's host: the same terminal, the same
// box, the same Stop). When the run ends the block folds back to the page's one-line result with "Show output".
//
// WHY the host is an <r2-out> element and not a div: morph() reuses an element of the same tag by position, rewriting only its
// attributes and children. A div host that folded away became the page's .d-body with the shadow root still on it (which hides
// every light child): a blank detail. An unknown tag is never matched with anything else, so morph replaces it whole.
// WHY the live part is a shadow root: the shell rewrites the detail by morph() every second and removes any node it
// did not draw. A terminal that is a light-DOM child of the run line would be torn out once a second and lose focus and
// scroll. The shell draws only an empty host element; a shadow root's contents are invisible to morph, so the terminal
// and the answer box keep their caret, selection and scrollback. The widget (a plain element tree) is moved into the
// new host's shadow root whenever the shell replaces the host (another entity, a re-layout).
// WHY a quiet query starts folded: "Check communication" lasts a second; its line says Show output, like today.
// WHY adopt(): the shell keeps a run's line only for runs this page started (S.util); after a reload there is none, and
// the contract has no hook for a run the page did not start. r2 gives the shell what it would have had (a util for the
// active run) so the line, the result and the Activity entry follow the shell's own flow. The clean fix is shell-side.
'use strict';
(function () {
  const ID = 'r2';
  const DEFAULT_HEIGHT = 220;
  const MIN_HEIGHT = 80;
  const PROMPT = /(\?|:|>|\])\s*$/;
  const YES_NO = /\[(y\/n|y\/N|Y\/n)\]|\((y\/n)\)/i;
  const CSS = `
.r2-line { gap: 8px; align-items: center; flex-wrap: wrap; }
.r2-line .ent-run-t { white-space: normal; overflow: visible; }
.r2-line b { font-weight: 600; color: var(--io-text-strong); }
.r2-line .r2-sp { flex: 1; }
.r2-line .r2-spin { align-self: center; }
.r2-spin { width: 9px; height: 9px; flex: none; border-radius: 50%; border: 2px solid var(--io-border); border-top-color: var(--io-text); animation: r2-spin 0.9s linear infinite; display: inline-block; }
.r2-spin.wait { animation: none; border-top-color: var(--io-border); border-style: dotted; }
@keyframes r2-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .r2-spin { animation: none; } }
.r2-tick { width: 12px; height: 12px; flex: none; align-self: center; fill: none; stroke: var(--io-muted); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.r2-stop { flex: none; border: 1px solid var(--io-border); background: var(--io-field); color: var(--io-text); border-radius: var(--io-radius-sm); padding: 2px 9px; font: inherit; font-size: 11.5px; cursor: pointer; }
.r2-stop:hover:not([disabled]) { background: var(--io-hover); }
.r2-stop.moves { background: var(--io-text-strong); border-color: var(--io-text-strong); color: var(--io-surface); }
.r2-stop.moves:hover:not([disabled]) { background: var(--io-text); }
.r2-stop[disabled] { opacity: .55; cursor: default; }
.r2-out { display: block; padding: 8px 18px 12px; background: var(--io-surface); border-bottom: 1px solid var(--io-border-soft); min-height: calc(var(--r2-h, ${DEFAULT_HEIGHT}px) + 56px); box-sizing: border-box; }
.r2-runs-wrap { position: relative; display: inline-flex; }
.r2-runs { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: var(--io-muted); font: inherit; font-size: 12px; padding: 2px 7px; border-radius: var(--io-radius-sm); cursor: pointer; }
.r2-runs:hover, .r2-runs[aria-expanded="true"] { background: var(--io-hover); color: var(--io-text); }
.r2-runs .ic { width: 13px; height: 13px; }
.r2-badge { min-width: 15px; height: 15px; padding: 0 4px; border-radius: 8px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10.5px; font-weight: 600; line-height: 15px; text-align: center; }
.r2-badge[hidden] { display: none; }
.r2-pop { position: absolute; left: 0; bottom: calc(100% + 6px); z-index: 60; width: 340px; max-height: 320px; overflow: auto; padding: 4px; background: var(--io-popover); color: var(--io-text);
  border: 1px solid var(--io-border); border-radius: var(--io-radius-lg); box-shadow: var(--io-shadow-pop); font-size: 12px; }
.r2-pop[hidden] { display: none; }
.r2-pop-h { padding: 4px 8px 2px; color: var(--io-faint); font-size: 10.5px; text-transform: uppercase; letter-spacing: .5px; }
.r2-pop a { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: var(--io-radius-sm); color: var(--io-text); cursor: pointer; text-decoration: none; }
.r2-pop a:hover, .r2-pop a:focus-visible { background: var(--io-hover); outline: none; }
.r2-pop .r2-pop-t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.r2-pop .r2-pop-m { color: var(--io-faint); font-family: var(--io-font-mono); font-size: 11px; }
.r2-pop .r2-none { padding: 8px; color: var(--io-faint); }
`;
  // What the widget inside the shadow root looks like. The page's tokens reach it (custom properties inherit through a
  // shadow boundary); xterm.css is added from vendor/xterm/xterm.css.
  const WIDGET_CSS = `
:host { display: block; }
* { box-sizing: border-box; }
.w { max-width: var(--io-detail-max, 760px); font: 400 12px/1.4 var(--io-font-ui); color: var(--io-text); }
.tw { height: var(--r2-h, ${DEFAULT_HEIGHT}px); border: 1px solid var(--io-border-soft); border-radius: var(--io-radius); background: var(--io-surface); padding: 6px 4px 2px 10px; overflow: hidden; }
.term { height: 100%; }
.term .xterm { height: 100%; }
.term .xterm-viewport { background: transparent !important; }
.grip { height: 8px; margin-top: 1px; cursor: row-resize; touch-action: none; outline: none; position: relative; }
.grip::before { content: ""; position: absolute; left: 40%; right: 40%; top: 3px; height: 2px; border-radius: 1px; background: var(--io-border-soft); transition: background .1s; }
.grip:hover::before, .grip:focus-visible::before, .grip.drag::before { background: var(--io-selected-line); }
.in { display: flex; align-items: center; gap: 6px; margin-top: 4px; }
.in[hidden] { display: none; }
.in .chev { color: var(--io-faint); font-family: var(--io-font-mono); }
.in input { flex: 1; min-width: 0; font: 12.5px var(--io-font-mono); color: var(--io-text); background: var(--io-field); border: 1px solid var(--io-border); border-radius: var(--io-radius-sm); padding: 5px 8px; outline: none; }
.in input:focus-visible { border-color: var(--io-focus); box-shadow: 0 0 0 2px var(--io-accent-soft); }
.in.waiting input { border-color: var(--io-selected-line); box-shadow: 0 0 0 2px var(--io-accent-soft); }
.in .ask { color: var(--io-text-strong); font-weight: 500; white-space: nowrap; }
.in .ask[hidden], .in .quick[hidden] { display: none; }
.in .quick { display: inline-flex; gap: 4px; }
button { border: 1px solid var(--io-border); background: var(--io-field); color: var(--io-text); border-radius: var(--io-radius-sm); padding: 4px 10px; font: inherit; font-size: 11.5px; cursor: pointer; }
button:hover:not([disabled]) { background: var(--io-hover); }
button:focus-visible { outline: var(--io-focus-width, 2px) solid var(--io-focus); outline-offset: 1px; }
button[disabled] { opacity: .55; cursor: default; }
.stop.moves { background: var(--io-text-strong); border-color: var(--io-text-strong); color: var(--io-surface); }
.stop.moves:hover:not([disabled]) { background: var(--io-text); }
.foot { display: flex; flex-wrap: wrap; gap: 4px 12px; margin-top: 6px; color: var(--io-faint); font-size: 11.5px; min-height: 16px; }
.foot a { color: var(--io-accent-text); }
.foot .bad { color: var(--io-error-text); }
`;

  // the entity's row label: the run's label minus the trailing " · <action title>" (an entity's own label may hold " · " too)
  function entityOf(run) {
    const tail = ` · ${String(run.title || '').replace(/…$/, '')}`;
    const label = String(run.label || '');
    return tail.length > 3 && label.endsWith(tail) ? label.slice(0, -tail.length) : (label || run.path);
  }

  const view = {
    name: 'Under the action',
    description: 'The Utilities run line expands in place into the live output, with an answer box and Stop beside it; Pop out opens it in its own window; a finished run folds back to its one-line result',
    order: 2,

    mount(ctx) {
      const s = { ctx, shown: new Set(), hidden: new Set(), stopping: new Set(), prev: new Map(), widgets: new Map(), adopted: new Set(), off: [], timer: null,
        height: Number(ctx.pref('r2.height', String(DEFAULT_HEIGHT))) || DEFAULT_HEIGHT, sheet: null, pop: false };
      this.s = s;
      ctx.css('r2', CSS);
      document.documentElement.style.setProperty('--r2-h', `${s.height}px`);
      ctx.statusSlot.innerHTML = `<span class="r2-runs-wrap"><button class="r2-runs" aria-haspopup="true" aria-expanded="false" title="Runs on this host: the ones going, the last finished">`
        + `<svg class="ic" aria-hidden="true"><use href="#i-tty"/></svg><span>Runs</span><span class="r2-badge" hidden></span></button><div class="r2-pop" role="menu" hidden></div></span>`;
      s.el = { button: ctx.statusSlot.querySelector('.r2-runs'), badge: ctx.statusSlot.querySelector('.r2-badge'), pop: ctx.statusSlot.querySelector('.r2-pop') };
      // the widget's stylesheet: xterm's own CSS plus ours, one constructed sheet every shadow root adopts
      s.cssReady = fetch('vendor/xterm/xterm.css').then((response) => response.text()).catch(() => '').then((xtermCss) => {
        s.sheet = new CSSStyleSheet();
        s.sheet.replaceSync(`${xtermCss}\n${WIDGET_CSS}`);
        this.update(ctx);
      });
      document.addEventListener('click', s.click = (event) => this.onClick(event), true);
      document.addEventListener('keydown', s.key = (event) => {
        if (event.key === 'Escape' && s.pop) { this.setPop(false); s.el.button.focus(); }
      });
      s.off.push(ctx.onChange(() => this.update(ctx)));
      s.timer = setInterval(() => this.tick(), 500);
      this.update(ctx);
    },

    unmount(ctx) {
      const s = this.s;
      if (!s) return;
      clearInterval(s.timer);
      s.off.forEach((off) => off());
      document.removeEventListener('click', s.click, true);
      document.removeEventListener('keydown', s.key);
      for (const widget of s.widgets.values()) this.dispose(widget);
      document.documentElement.style.removeProperty('--r2-h');
      ctx.statusSlot.innerHTML = '';
      this.s = null;
      ctx.redraw();
    },

    // ------------------------------------------------------------------------------------------ the seam ----
    // "Show output": the run's block opens (or opens again) under the action.
    open(id, ctx) {
      const s = this.s;
      if (!s || !ctx.run(id)) return;
      s.hidden.delete(id);
      s.shown.add(id);
      ctx.redraw();
    },

    // The run line. null = the shell's own line (a folded run's quiet result with "Show output").
    runLine(util, run, ctx) {
      const s = this.s;
      if (!s) return null;
      this.track(ctx);
      const h = ctx.h;
      const title = `<b>${h.esc(h.plainTitle(util.title))}</b> on ${h.esc(util.label)}`;
      if (!this.isOpen(run)) {
        if (!run.active) return null;
        const wait = run.state === 'queued' || run.state === 'waiting';
        return `<div class="ent-run running r2-line" role="status" aria-live="polite" data-r2-line="${h.esc(run.id)}"><span class="r2-spin${wait ? ' wait' : ''}" role="img" aria-label="${h.esc(h.stateWords(run))}"></span>`
          + `<span class="ent-run-t">${title} · ${h.esc(h.stateWords(run))} ${h.esc(h.elapsed(run))}</span><span class="r2-sp"></span>`
          + `<button class="ent-run-more" data-run-open="${h.esc(run.id)}">Show output</button>`
          + `${run.interactive ? this.stopButton(run, ctx) : ''}</div>`;
      }
      let lead;
      let words;
      if (run.active) {
        const wait = run.state === 'queued' || run.state === 'waiting';
        lead = `<span class="r2-spin${wait ? ' wait' : ''}" role="img" aria-label="${h.esc(h.stateWords(run))}"></span>`;
        words = `${h.esc(h.stateWords(run))} ${h.esc(h.elapsed(run))}`;
      } else {
        const kind = { failed: 'error', stopped: 'warn', timed_out: 'warn' }[run.state];
        lead = kind ? h.shape(kind, h.stateWords(run)) : '<svg class="r2-tick" viewBox="0 0 16 16" aria-hidden="true"><path d="m3.5 8.5 3 3 6-7"/></svg>';
        const message = run.result && run.result.message ? ` · ${h.esc(run.result.message)}` : '';
        words = `${h.esc(h.stateWords(run))}${run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${h.esc(run.exit_code)}` : ''} · ${h.esc(h.elapsed(run))}${message}`;
      }
      return `<div class="ent-run ${run.active ? 'running' : (run.state === 'failed' ? 'fail' : 'ok')} r2-line" role="status" data-r2-line="${h.esc(run.id)}"${run.state === 'failed' ? ' data-diagnostic' : ''}>${lead}`
        + `<span class="ent-run-t">${title} · ${words}</span><span class="r2-sp"></span>`
        + `<button class="ent-run-more" data-r2-popout="${h.esc(run.id)}" title="Open this run in its own window">Pop out</button>`
        + `<button class="ent-run-more" data-r2-hide="${h.esc(run.id)}">Hide output</button></div>`
        + `<r2-out class="r2-out" data-r2-host="${h.esc(run.id)}"></r2-out>`;
    },

    // Called after every page render and when a run changes: keep the hosts filled, the widgets honest, the badge right.
    update(ctx) {
      const s = this.s;
      if (!s) return;
      this.track(ctx);
      if (this.adopt(ctx)) ctx.redraw();
      this.fill();
      this.collect();
      this.paintStatus();
    },

    // ------------------------------------------------------------------------------------------ state ----
    // Open = the block shows. A run going is open unless the person hid it (a quiet query starts folded); a finished
    // run is open only while the person asks to see it.
    isOpen(run) {
      const s = this.s;
      if (run.active) return !s.hidden.has(run.id) && (run.danger !== 'query' || s.shown.has(run.id));
      return s.shown.has(run.id);
    },

    // WHY folded at the end: "Finished output collapses back to the one-line result with Show output".
    track(ctx) {
      const s = this.s;
      for (const run of ctx.runs()) {
        const was = s.prev.get(run.id);
        if (was === true && !run.active) { s.shown.delete(run.id); s.hidden.delete(run.id); s.stopping.delete(run.id); }
        s.prev.set(run.id, run.active);
      }
    },

    // A run going with no util on this page (the page was reloaded, or another window started it): hand the shell one.
    adopt(ctx) {
      const s = this.s;
      let page;
      try { page = typeof S !== 'undefined' ? S : null; } catch (_) { page = null; }
      if (!page || !page.runs) return false;
      const known = new Set([page.util, ...page.runs.values()].filter(Boolean).map((util) => util.runId));
      let changed = false;
      for (const run of ctx.runs()) {
        if (!run.active || known.has(run.id) || s.adopted.has(run.id)) continue;
        s.adopted.add(run.id);
        const util = { path: run.path, name: run.action, node: run.node ?? null, newId: run.new_id ?? null, phase: 'running', dry: null, result: null, error: '', refused: '',
          ticked: true, title: run.title, danger: run.danger, owner: run.plugin, label: entityOf(run), runId: run.id,
          startedAt: Date.now() - (run.elapsed_s || 0) * 1000, adopted: true };
        const mine = page.util && page.util.path === run.path;
        if (page.sel === run.path && !page.selKey && (!page.util || (mine && page.util.phase === 'done'))) page.util = util;
        else page.runs.set(run.path, util);
        changed = true;
      }
      return changed;
    },

    // ------------------------------------------------------------------------------------------ hosts ----
    fill() {
      const s = this.s;
      if (!s.sheet) return;
      for (const host of document.querySelectorAll('[data-r2-host]')) {
        const id = host.dataset.r2Host;
        const run = s.ctx.run(id);
        if (!run) continue;
        let widget = s.widgets.get(id);
        if (!widget) { widget = this.makeWidget(id); s.widgets.set(id, widget); }
        if (!host.shadowRoot) {
          const shadow = host.attachShadow({ mode: 'open' });
          shadow.adoptedStyleSheets = [s.sheet];
        }
        // WHY the sweep: morph() reuses a host element by position and only rewrites its attributes, so the element that held
        // another run's widget can now stand for this run: whatever else it holds goes back to its own host on the next fill
        for (const child of [...host.shadowRoot.children]) if (child !== widget.root) child.remove();
        if (widget.root.parentNode !== host.shadowRoot) host.shadowRoot.appendChild(widget.root);
        widget.host = host;
        if (!widget.bound) {
          widget.bound = s.ctx.terminal(widget.term, id, { fontSize: 12 });
          requestAnimationFrame(() => widget.bound && widget.bound.fit());
          // a prompt is read from the terminal's own cursor line as soon as the bytes are parsed, not at the next tick
          if (widget.bound.term) widget.bound.term.onWriteParsed(() => { if (!widget.paintQueued) { widget.paintQueued = true; requestAnimationFrame(() => { widget.paintQueued = false; const now = s.ctx.run(id); if (now && this.s) this.paintWidget(widget, now); }); } });
        }
        this.paintWidget(widget, run);
      }
    },

    // A widget whose block is folded goes away; one whose host left the page (another entity) goes away once its run ended.
    collect() {
      const s = this.s;
      for (const [id, widget] of [...s.widgets]) {
        const run = s.ctx.run(id);
        const attached = widget.host && widget.host.isConnected && widget.host.shadowRoot && widget.root.parentNode === widget.host.shadowRoot;
        if (!run || !this.isOpen(run) || (!attached && !run.active)) { this.dispose(widget); s.widgets.delete(id); }
      }
    },

    dispose(widget) {
      if (widget.bound) widget.bound.dispose();
      widget.bound = null;
      widget.root.remove();
    },

    tick() {
      const s = this.s;
      if (!s) return;
      for (const [id, widget] of s.widgets) {
        const run = s.ctx.run(id);
        if (run) this.paintWidget(widget, run);
      }
      this.paintStatus();
    },

    // ------------------------------------------------------------------------------------------ the widget ----
    makeWidget(id) {
      const s = this.s;
      const root = document.createElement('div');
      root.className = 'w';
      root.innerHTML = `<div class="tw"><div class="term"></div></div>`
        + `<div class="grip" role="separator" aria-orientation="horizontal" aria-label="Resize the output" tabindex="0" title="Drag to resize · double-click to reset"></div>`
        + `<form class="in" hidden autocomplete="off"><span class="chev" aria-hidden="true">›</span>`
        + `<input type="text" name="answer" aria-label="Answer for the process" placeholder="Type an answer and press Enter" spellcheck="false" autocomplete="off">`
        + `<span class="ask" hidden>Waiting for your answer</span>`
        + `<button type="submit" class="send">Send</button><span class="quick" hidden><button type="button" data-key="y">Yes</button><button type="button" data-key="n">No</button></span>`
        + `<button type="button" class="stop" hidden></button></form>`
        + `<div class="foot" role="status"></div>`;
      const widget = { id, root, term: root.querySelector('.term'), form: root.querySelector('.in'), input: root.querySelector('input'), ask: root.querySelector('.ask'),
        quick: root.querySelector('.quick'), stop: root.querySelector('.stop'), foot: root.querySelector('.foot'), grip: root.querySelector('.grip'), bound: null, host: null };
      widget.form.addEventListener('submit', (event) => {
        event.preventDefault();
        if (!widget.input.value) return;
        const text = `${widget.input.value}\n`;
        widget.input.value = '';
        s.ctx.sendInput(id, text);
      });
      widget.quick.addEventListener('click', (event) => {
        const key = event.target.closest('[data-key]');
        if (key) s.ctx.sendInput(id, `${key.dataset.key}\n`);
      });
      widget.stop.addEventListener('click', () => this.stop(id));
      // WHY the page's own key handlers stay out of it: Escape and `]` are the page's, a terminal program wants them
      root.addEventListener('keydown', (event) => event.stopPropagation());
      this.wireGrip(widget);
      return widget;
    },

    wireGrip(widget) {
      const s = this.s;
      let drag = null;
      const apply = (height, persist) => {
        const max = Math.max(MIN_HEIGHT, window.innerHeight - 200);
        const value = Math.round(Math.max(MIN_HEIGHT, Math.min(height, max)));
        s.height = value;
        document.documentElement.style.setProperty('--r2-h', `${value}px`);
        if (persist) s.ctx.setPref('r2.height', String(value));
        return value;
      };
      widget.grip.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        drag = { id: event.pointerId, y: event.clientY, h: s.height };
        widget.grip.setPointerCapture(event.pointerId);
        widget.grip.classList.add('drag');
        document.body.classList.add('resizing');
        event.preventDefault();
      });
      widget.grip.addEventListener('pointermove', (event) => {
        if (drag && event.pointerId === drag.id) drag.next = apply(drag.h + (event.clientY - drag.y), false);
      });
      const end = (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        if (drag.next !== undefined) apply(drag.next, true);
        drag = null;
        widget.grip.classList.remove('drag');
        document.body.classList.remove('resizing');
      };
      widget.grip.addEventListener('pointerup', end);
      widget.grip.addEventListener('pointercancel', end);
      widget.grip.addEventListener('dblclick', () => { s.ctx.setPref('r2.height', ''); apply(DEFAULT_HEIGHT, false); });
      widget.grip.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        apply(s.height + (event.key === 'ArrowDown' ? 16 : -16), true);
        event.preventDefault();
      });
    },

    // the line the cursor is on, read from the terminal's buffer: a prompt waits at its end
    cursorLine(widget) {
      const term = widget.bound && widget.bound.term;
      if (!term) return '';
      const buffer = term.buffer.active;
      const line = buffer.getLine(buffer.baseY + buffer.cursorY);
      return line ? line.translateToString(true) : '';
    },

    paintWidget(widget, run) {
      const s = this.s;
      const h = s.ctx.h;
      const interactive = run.active && run.interactive;
      widget.form.hidden = !interactive;
      if (interactive) {
        const line = this.cursorLine(widget);
        const waiting = PROMPT.test(line.trimEnd()) && line.trim().length > 1;
        widget.form.classList.toggle('waiting', waiting);
        widget.ask.hidden = !waiting;
        widget.ask.textContent = waiting ? 'Waiting for your answer' : '';
        widget.quick.hidden = !(waiting && YES_NO.test(line));
        const moves = run.danger === 'moves';
        const stopping = s.stopping.has(run.id) || run.stopping;
        widget.stop.hidden = false;
        widget.stop.className = `stop${moves ? ' moves' : ''}`;
        widget.stop.disabled = stopping;
        widget.stop.dataset.r2Stop = run.id;
        widget.stop.textContent = stopping ? 'Stopping…' : (moves ? 'Stop (motor goes limp)' : 'Stop');
        widget.stop.title = moves ? 'Interrupt it, then the plugin\'s own stop sends the board mode 0 (it goes limp)' : 'Interrupt it (SIGINT, then SIGTERM, then SIGKILL)';
      }
      let text;
      if (run.active && run.interactive) text = widget.form.classList.contains('waiting') ? 'The process is waiting for an answer: type it here or in the output' : 'Typing in the output or in this box goes to the process · Ctrl-C stops it';
      else if (run.active) text = 'Started from another window of this host: you can watch it here, not type into it';
      else {
        const bad = run.state === 'failed' || run.state === 'timed_out';
        const stop = run.stop && run.stop.plugin_stop && run.stop.plugin_stop.ran ? `<span>the plugin's stop: ${h.esc(run.stop.plugin_stop.message || run.stop.plugin_stop.outcome)}</span>` : '';
        text = `<span class="${bad ? 'bad' : ''}">${h.esc(h.stateWords(run))}${run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${h.esc(run.exit_code)}` : ''} · ${h.esc(h.elapsed(run))}</span>${stop}`
          + `<a href="${h.esc(s.ctx.castUrl(run.id))}" download="${h.esc(run.id)}.cast" title="An asciinema recording: replay it with asciinema play">recording (.cast)</a>`;
      }
      if (widget.footHtml !== text) { widget.footHtml = text; widget.foot.innerHTML = text; }
    },

    // ------------------------------------------------------------------------------------------ actions ----
    stopButton(run, ctx) {
      const s = this.s;
      const moves = run.danger === 'moves';
      const stopping = s.stopping.has(run.id) || run.stopping;
      return `<button class="r2-stop${moves ? ' moves' : ''}" data-r2-stop="${ctx.h.esc(run.id)}"${stopping ? ' disabled' : ''}>${stopping ? 'Stopping…' : (moves ? 'Stop (motor goes limp)' : 'Stop')}</button>`;
    },

    async stop(id) {
      const s = this.s;
      s.stopping.add(id);
      this.update(s.ctx);
      s.ctx.redraw();
      const answer = await s.ctx.interrupt(id);
      if (!answer.ok) {
        s.stopping.delete(id);
        const widget = s.widgets.get(id);
        if (widget) widget.foot.textContent = `Stop was not sent: ${(answer.data && (answer.data.error || answer.data.refused)) || `HTTP ${answer.status}`}`;
        s.ctx.redraw();
      }
    },

    onClick(event) {
      const s = this.s;
      const target = event.target;
      if (!target || !target.closest) return;
      const stop = target.closest('[data-r2-stop]');
      if (stop && !stop.disabled) { this.stop(stop.dataset.r2Stop); return; }
      const pop = target.closest('[data-r2-popout]');
      if (pop) { this.popOut(pop.dataset.r2Popout); return; }
      const hide = target.closest('[data-r2-hide]');
      if (hide) { s.hidden.add(hide.dataset.r2Hide); s.shown.delete(hide.dataset.r2Hide); s.ctx.redraw(); return; }
      if (target.closest('.r2-runs')) { this.setPop(!s.pop); return; }
      const show = target.closest('[data-r2-show]');
      if (show) {
        // the shell's own [data-go] handler takes the person to the run's entity; the block is already waiting open there
        s.hidden.delete(show.dataset.r2Show);
        s.shown.add(show.dataset.r2Show);
        this.setPop(false);
        return;
      }
      if (s.pop && !target.closest('.r2-runs-wrap')) this.setPop(false);
    },

    // WHY a named window: the same run asked for again focuses the window it is already in
    popOut(id) {
      window.open(`runs/r2-popout.html?run=${encodeURIComponent(id)}`, `io-diagnostics-run-${id}`, 'popup=yes,width=920,height=600');
    },

    // ------------------------------------------------------------------------------------------ the status bar ----
    setPop(open) {
      const s = this.s;
      s.pop = open;
      s.el.pop.hidden = !open;
      s.el.button.setAttribute('aria-expanded', String(open));
      this.paintStatus();
    },

    paintStatus() {
      const s = this.s;
      const runs = s.ctx.runs();
      const going = runs.filter((run) => run.active).length;
      s.el.badge.hidden = !going;
      s.el.badge.textContent = String(going);
      s.el.button.title = going ? `Runs: ${going} going` : 'Runs on this host';
      if (!s.pop) return;
      const h = s.ctx.h;
      const recent = [...runs.filter((run) => run.active), ...runs.filter((run) => !run.active).slice(0, 6)];
      const row = (run) => `<a role="menuitem" tabindex="0" data-go="${h.esc(run.path)}" data-r2-show="${h.esc(run.id)}" title="${h.esc(run.label)} · ${h.esc(h.stateWords(run))}">`
        + `${run.active ? '<span class="r2-spin"></span>' : ''}<span class="r2-pop-t">${h.esc(run.label)}</span><span class="r2-pop-m">${h.esc(h.stateWords(run))} · ${h.esc(h.elapsed(run))}</span></a>`;
      const html = `<div class="r2-pop-h">Runs · pick one to see its output under its action</div>${recent.length ? recent.map(row).join('') : '<div class="r2-none">No runs yet.</div>'}`;
      if (s.popHtml !== html) { s.popHtml = html; s.el.pop.innerHTML = html; }
    },
  };

  window.IORunViews = window.IORunViews || {};
  window.IORunViews[ID] = view;
}());
