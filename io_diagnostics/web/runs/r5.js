// r5 · A tmux session (docs/runs-v1.md section 7): a tmux-like surface over the runs stream.
//
// Every run is a window of one session, "robotdx". The selected window fills the panel; a status line under it lists the
// windows the way tmux does (`0:calibrate@1* 1:check_comms-`, `#` activity, `!` ended while you looked elsewhere), the
// prefix is Ctrl-b: n / p next and previous window, l last, 0-9 by number, w a list to choose from, & closes a finished
// window, d detaches the view (the runs go on; the status bar says "detached" and one click attaches again), ? the keys,
// Ctrl-b Ctrl-b sends a literal Ctrl-b to the process. Input goes to the process while it runs. "Attach from a terminal"
// copies the one-line `tmux attach` command.
//
// WHY an in-page imitation and not a tmux server (Zach's brief for r5): the host runs acts exactly as before (docs/runs-v1.md
// section 3, a PTY it owns); this view only draws them with tmux's grammar over the same stream. So the copied command is
// the one a real tmux backend would answer to, and today nothing answers it: the button says so when it copies. What a real
// backend needs is in the report that came with this view.
// WHY finished windows stay (tmux's remain-on-exit): the output of a run that ended is what you came to read; Ctrl-b & closes it.
// WHY a watcher stream per running window: a window you are not looking at still shows `#` when it prints, as tmux does
// with monitor-activity; the terminal itself is made only for a window that has been shown.
'use strict';
(function () {
  const ID = 'r5';
  const SESSION = 'robotdx';
  const ATTACH = `tmux attach -t ${SESSION}`;
  const DEFAULT_HEIGHT = 320;
  const MIN_HEIGHT = 140;
  const KEYS = [['Ctrl-b n', 'next window'], ['Ctrl-b p', 'previous window'], ['Ctrl-b l', 'last window'], ['Ctrl-b 0-9', 'window by number'], ['Ctrl-b w', 'choose a window from a list'],
    ['Ctrl-b &', 'close a finished window'], ['Ctrl-b d', 'detach (the runs keep going)'], ['Ctrl-b Ctrl-b', 'send Ctrl-b to the process'], ['Ctrl-b ?', 'this list'], ['Ctrl-b Esc', 'cancel the prefix']];
  const CSS = `
#runs-root.r5 { position: relative; flex: none; display: flex; flex-direction: column; height: var(--r5-h, ${DEFAULT_HEIGHT}px); border-top: 1px solid var(--io-border); background: var(--io-surface); min-height: 0; }
#runs-root.r5[hidden] { display: none; }
.r5-sash { position: absolute; left: 0; right: 0; top: -4px; height: 8px; z-index: 5; cursor: row-resize; touch-action: none; outline: none; }
.r5-sash::before { content: ""; position: absolute; left: 0; right: 0; top: 3px; height: 1px; background: transparent; transition: background .1s, height .1s, top .1s; }
.r5-sash:hover::before, .r5-sash:focus-visible::before, body.resizing .r5-sash::before { top: 2px; height: 3px; background: var(--io-selected-line); }
.r5-body { position: relative; flex: 1; min-height: 0; background: var(--io-surface); }
.r5-pane { position: absolute; inset: 0; padding: 6px 4px 2px 12px; }
.r5-pane[hidden] { display: none; }
.r5-pane .xterm { height: 100%; }
.r5-pane .xterm-viewport { background: transparent !important; }
.r5-empty { padding: 18px 14px; color: var(--io-faint); font-size: 12.5px; line-height: 1.6; }
.r5-empty code, .r5-pop code { font-family: var(--io-font-mono); font-size: 12px; background: var(--io-control); padding: 0 4px; border-radius: var(--io-radius-sm); }
.r5-status { flex: none; display: flex; align-items: center; gap: 10px; min-height: 28px; padding: 0 8px 0 10px; border-top: 1px solid var(--io-border-soft); background: var(--io-surface-2); color: var(--io-muted); font: 12px/1 var(--io-font-mono); }
.r5-left { flex: none; color: var(--io-text-strong); font-weight: 600; }
.r5-wins { flex: 1; min-width: 0; display: flex; align-items: center; gap: 2px; overflow-x: auto; scrollbar-width: none; }
.r5-win { flex: none; display: inline-flex; align-items: center; gap: 0; padding: 4px 8px; border: 0; border-radius: var(--io-radius-sm); background: transparent; color: var(--io-muted); font: inherit; cursor: pointer; white-space: nowrap; outline: none; }
.r5-win:hover { background: var(--io-hover); color: var(--io-text); }
.r5-win:focus-visible { box-shadow: 0 0 0 2px var(--io-focus); }
.r5-win[aria-selected="true"] { background: var(--io-text-strong); color: var(--io-surface); }
.r5-win.dead .r5-name { opacity: .8; }
.r5-win .shape { width: 11px; height: 11px; vertical-align: 0; margin-left: 6px; }
.r5-win[aria-selected="true"] .shape { color: inherit; }
.r5-flag { opacity: .85; }
.r5-right { flex: none; display: flex; align-items: center; gap: 8px; }
.r5-title { max-width: 260px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-faint); }
.r5-prefix { padding: 3px 6px; border-radius: var(--io-radius-sm); background: var(--io-accent); color: var(--io-surface); font-weight: 600; }
.r5-prefix[hidden], .r5-msg[hidden] { display: none; }
.r5-msg { max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--io-text); }
.r5-btn { border: 1px solid var(--io-border); background: var(--io-field); color: var(--io-text); border-radius: var(--io-radius-sm); padding: 3px 9px; font: 12px var(--io-font-ui); cursor: pointer; white-space: nowrap; }
.r5-btn:hover:not([disabled]) { background: var(--io-hover); }
.r5-btn:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.r5-btn[disabled] { opacity: .55; cursor: default; }
.r5-btn[hidden] { display: none; }
.r5-btn.moves { background: var(--io-text-strong); border-color: var(--io-text-strong); color: var(--io-surface); }
.r5-btn.moves:hover:not([disabled]) { background: var(--io-text); }
.r5-pop { position: absolute; left: 12px; bottom: 8px; z-index: 6; min-width: 300px; max-width: calc(100% - 24px); max-height: calc(100% - 16px); overflow: auto; padding: 6px; background: var(--io-popover); color: var(--io-text);
  border: 1px solid var(--io-border); border-radius: var(--io-radius-lg); box-shadow: var(--io-shadow-pop); font: 12px/1.4 var(--io-font-ui); }
.r5-pop[hidden] { display: none; }
.r5-pop-h { padding: 4px 8px 6px; color: var(--io-faint); font-size: 10.5px; text-transform: uppercase; letter-spacing: .5px; }
.r5-pop-row { display: flex; align-items: center; gap: 10px; padding: 5px 8px; border-radius: var(--io-radius-sm); cursor: pointer; }
.r5-pop-row[aria-selected="true"], .r5-pop-row:hover { background: var(--io-hover); }
.r5-pop-row .k { flex: none; min-width: 108px; font-family: var(--io-font-mono); font-size: 11.5px; color: var(--io-text-strong); }
.r5-pop-row .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.r5-pop-row .m { color: var(--io-faint); font-family: var(--io-font-mono); font-size: 11px; }
.r5-toggle { display: inline-flex; align-items: center; gap: 5px; border: 0; background: transparent; color: var(--io-muted); font: inherit; font-size: 12px; padding: 2px 7px; border-radius: var(--io-radius-sm); cursor: pointer; }
.r5-toggle:hover { background: var(--io-hover); color: var(--io-text); }
.r5-toggle[aria-pressed="true"] { color: var(--io-text-strong); }
.r5-toggle .ic { width: 13px; height: 13px; }
.r5-badge { min-width: 15px; height: 15px; padding: 0 4px; border-radius: 8px; background: var(--io-text-strong); color: var(--io-surface); font-size: 10.5px; font-weight: 600; line-height: 15px; text-align: center; }
.r5-badge[hidden] { display: none; }
.r5-detached { color: var(--io-faint); font-size: 11.5px; }
`;

  // the window's name, as tmux shows it: the action, @ the board it drove
  const nameOf = (run) => `${run.action}${run.node !== null && run.node !== undefined ? `@${run.node}` : ''}`;

  const view = {
    name: 'A real terminal (tmux)',
    description: 'A tmux-like session over the runs stream: one window per run, a status line listing them, Ctrl-b n / p / d, input while it runs, and a button that copies the tmux attach command',
    order: 5,

    mount(ctx) {
      let saved = [];
      try { saved = JSON.parse(ctx.pref('r5.windows', '[]')); } catch (_) { saved = []; }
      if (!Array.isArray(saved)) saved = [];
      const s = { ctx, open: ctx.pref('r5.open', '0') === '1', height: Number(ctx.pref('r5.height', String(DEFAULT_HEIGHT))) || DEFAULT_HEIGHT, selected: ctx.pref('r5.selected', '') || null, last: null,
        windows: new Map(), saved, restored: false, closed: new Set(), stopping: new Set(), off: [], timer: null, prefix: false, pop: null, message: '', messageAt: 0, statusSig: '', clockSig: '' };
      this.s = s;
      ctx.css('r5', CSS);
      const root = ctx.root;
      root.classList.add('r5');
      root.innerHTML = `<div class="r5-sash" role="separator" aria-orientation="horizontal" aria-label="Resize the session" tabindex="0" title="Drag to resize · double-click to reset"></div>`
        + `<div class="r5-body"><div class="r5-empty"></div><div class="r5-pop" role="dialog" hidden></div></div>`
        + `<div class="r5-status" role="status" aria-label="tmux session ${SESSION}"><span class="r5-left">[${SESSION}]</span><div class="r5-wins" role="tablist" aria-label="Windows"></div>`
        + `<div class="r5-right"><span class="r5-msg" hidden></span><span class="r5-prefix" hidden title="The prefix was pressed: the next key is a tmux key">^B</span>`
        + `<button class="r5-btn r5-stop" hidden></button><span class="r5-title"></span><button class="r5-btn r5-attach" title="Copies the command to attach from a terminal">Attach from a terminal</button>`
        + `<button class="r5-btn r5-detach" title="Detach the view (Ctrl-b d): the runs keep going">Detach</button><span class="r5-clock"></span></div></div>`;
      s.el = { sash: root.querySelector('.r5-sash'), body: root.querySelector('.r5-body'), empty: root.querySelector('.r5-empty'), pop: root.querySelector('.r5-pop'), wins: root.querySelector('.r5-wins'),
        msg: root.querySelector('.r5-msg'), prefix: root.querySelector('.r5-prefix'), stop: root.querySelector('.r5-stop'), title: root.querySelector('.r5-title'), attach: root.querySelector('.r5-attach'),
        detach: root.querySelector('.r5-detach'), clock: root.querySelector('.r5-clock') };
      ctx.statusSlot.innerHTML = `<button class="r5-toggle" aria-pressed="false" aria-expanded="false" aria-controls="runs-root" title="tmux session ${SESSION}: click to attach">`
        + `<svg class="ic" aria-hidden="true"><use href="#i-tty"/></svg><span class="r5-toggle-t">tmux</span><span class="r5-badge" hidden></span><span class="r5-detached" hidden>detached</span></button>`;
      s.el.toggle = ctx.statusSlot.querySelector('.r5-toggle');
      s.el.badge = s.el.toggle.querySelector('.r5-badge');
      s.el.detached = s.el.toggle.querySelector('.r5-detached');
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
      for (const win of s.windows.values()) this.dispose(win);
      document.removeEventListener('keydown', s.key, true);
      window.removeEventListener('resize', s.onResize);
      document.body.classList.remove('resizing');
      ctx.root.classList.remove('r5');
      ctx.root.style.removeProperty('--r5-h');
      ctx.root.innerHTML = '';
      ctx.root.hidden = true;
      ctx.statusSlot.innerHTML = '';
      this.s = null;
    },

    // ------------------------------------------------------------------------------------------ the seam ----
    // Show a run: its window exists, is the current one, and the view is attached.
    open(id, ctx) {
      const s = this.s;
      if (!s || !ctx.run(id)) return;
      s.closed.delete(id);
      this.ensure(ctx.run(id));
      this.attach(false);
      this.select(id, true);
    },

    update(ctx) {
      const s = this.s;
      if (!s) return;
      const runs = ctx.runs();
      if (!s.restored && runs.length) {
        s.restored = true;
        // WHY the saved number: a session keeps its window numbers across a reload of the page that shows it
        for (const [id, index] of s.saved) { const run = ctx.run(id); if (run && !s.closed.has(id)) this.ensure(run, index); }
      }
      for (const run of runs) {
        const win = s.windows.get(run.id);
        // a run going is a window (a reload finds it again); a window's run that ended while it was not the current one rings
        if (run.active && !win && !s.closed.has(run.id)) this.ensure(run);
        if (win) {
          if (win.wasActive && !run.active) { if (s.selected !== run.id) win.ended = true; s.stopping.delete(run.id); }
          win.wasActive = run.active;
          if (run.active && !win.watch) this.watch(win);
          if (!run.active && win.watch) { win.watch(); win.watch = null; }
        }
      }
      for (const [id, win] of [...s.windows]) if (!ctx.run(id) && runs.length) { this.dispose(win); s.windows.delete(id); if (s.selected === id) s.selected = null; }
      if ((!s.selected || !s.windows.has(s.selected)) && s.windows.size) s.selected = this.sorted()[0].id;
      this.paint();
    },

    // ------------------------------------------------------------------------------------------ windows ----
    sorted() { return [...this.s.windows.values()].sort((a, b) => a.index - b.index); },

    // tmux gives a new window the lowest free index
    ensure(run, wanted) {
      const s = this.s;
      if (s.windows.has(run.id)) return s.windows.get(run.id);
      const used = new Set([...s.windows.values()].map((win) => win.index));
      let index = Number.isInteger(wanted) && !used.has(wanted) ? wanted : 0;
      while (used.has(index)) index += 1;
      const pane = document.createElement('div');
      pane.className = 'r5-pane';
      pane.dataset.r5Pane = run.id;
      pane.hidden = true;
      s.el.body.insertBefore(pane, s.el.pop);
      const win = { id: run.id, index, pane, bound: null, watch: null, activity: false, ended: false, wasActive: run.active, born: performance.now() };
      s.windows.set(run.id, win);
      this.saveWindows();
      if (run.active) this.watch(win);
      return win;
    },

    // activity in a window nobody is looking at: its own stream, counted from a moment after it starts (the replay is not news)
    watch(win) {
      const s = this.s;
      win.watch = s.ctx.stream(win.id, () => {
        if (s.selected !== win.id && performance.now() - win.born > 800 && !win.activity) { win.activity = true; this.paint(); }
      }, () => { win.watch = null; });
    },

    saveWindows() { this.s.ctx.setPref('r5.windows', JSON.stringify(this.sorted().map((win) => [win.id, win.index]))); },

    dispose(win) {
      if (win.watch) win.watch();
      win.watch = null;
      if (win.bound) win.bound.dispose();
      win.bound = null;
      win.pane.remove();
    },

    select(id, focus) {
      const s = this.s;
      const win = s.windows.get(id);
      if (!win) return;
      if (s.selected !== id) { s.last = s.selected; s.selected = id; }
      s.ctx.setPref('r5.selected', id);
      win.activity = false;
      win.ended = false;
      this.paint();
      requestAnimationFrame(() => { const bound = win.bound; if (bound) { bound.fit(); if (focus) bound.focus(); } });
    },

    // by order of the list: step = +1 next, -1 previous (wraps, as tmux does)
    step(step) {
      const list = this.sorted();
      if (list.length < 2) return;
      const at = list.findIndex((win) => win.id === this.s.selected);
      this.select(list[(at + step + list.length) % list.length].id, true);
    },

    close(id) {
      const s = this.s;
      const run = s.ctx.run(id);
      const win = s.windows.get(id);
      if (!win) return;
      if (run && run.active) { this.say(`window ${win.index} is still running: stop it first`); return; }
      this.dispose(win);
      s.windows.delete(id);
      s.closed.add(id);
      this.saveWindows();
      if (s.selected === id) s.selected = (s.last && s.windows.has(s.last) ? s.last : (this.sorted()[0] || {}).id) || null;
      this.paint();
      if (s.selected) this.select(s.selected, true);
    },

    // ------------------------------------------------------------------------------------------ attach / detach ----
    attach(focus) {
      const s = this.s;
      s.open = true;
      s.ctx.setPref('r5.open', '1');
      this.paint();
      requestAnimationFrame(() => { const win = s.windows.get(s.selected); if (win && win.bound) { win.bound.fit(); if (focus) win.bound.focus(); } });
    },
    detach() {
      const s = this.s;
      s.open = false;
      s.prefix = false;
      s.pop = null;
      s.ctx.setPref('r5.open', '0');
      this.say('');
      this.paint();
    },

    // ------------------------------------------------------------------------------------------ keys ----
    wire() {
      const s = this.s;
      const root = s.ctx.root;
      s.el.toggle.addEventListener('click', () => (s.open ? this.detach() : this.attach(true)));
      s.el.detach.addEventListener('click', () => this.detach());
      s.el.attach.addEventListener('click', () => this.copyAttach());
      s.el.stop.addEventListener('click', () => this.stop());
      s.el.wins.addEventListener('click', (event) => {
        const tab = event.target.closest('[data-r5-win]');
        if (tab) this.select(tab.dataset.r5Win, true);
      });
      s.el.wins.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
        this.step(event.key === 'ArrowRight' ? 1 : -1);
        const tab = s.el.wins.querySelector('[aria-selected="true"]');
        if (tab) tab.focus();
        event.preventDefault();
      });
      s.el.pop.addEventListener('click', (event) => {
        const row = event.target.closest('[data-r5-choose]');
        if (row) { s.pop = null; this.select(row.dataset.r5Choose, true); this.paint(); }
      });
      // WHY capture on the document: the prefix is tmux's, taken before the terminal (or the page) sees the key — but only while
      // the view is attached and the keyboard is in it (or the prefix is already pressed)
      document.addEventListener('keydown', s.key = (event) => this.onKey(event), true);
      // the top edge drags (remembered, double-click resets, arrow keys move it)
      let drag = null;
      s.el.sash.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        drag = { id: event.pointerId };
        s.el.sash.setPointerCapture(event.pointerId);
        document.body.classList.add('resizing');
        event.preventDefault();
      });
      s.el.sash.addEventListener('pointermove', (event) => { if (drag && event.pointerId === drag.id) drag.height = this.applyHeight(root.getBoundingClientRect().bottom - event.clientY, false); });
      const end = (event) => {
        if (!drag || event.pointerId !== drag.id) return;
        if (drag.height !== undefined) this.applyHeight(drag.height, true);
        drag = null;
        document.body.classList.remove('resizing');
      };
      s.el.sash.addEventListener('pointerup', end);
      s.el.sash.addEventListener('pointercancel', end);
      s.el.sash.addEventListener('dblclick', () => { s.ctx.setPref('r5.height', ''); this.applyHeight(DEFAULT_HEIGHT, false); });
      s.el.sash.addEventListener('keydown', (event) => {
        if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
        this.applyHeight(s.height + (event.key === 'ArrowUp' ? 16 : -16), true);
        event.preventDefault();
      });
      window.addEventListener('resize', s.onResize = () => this.applyHeight(s.height, false));
    },

    applyHeight(height, persist) {
      const s = this.s;
      const value = Math.round(Math.max(MIN_HEIGHT, Math.min(height, Math.max(MIN_HEIGHT, window.innerHeight - 200))));
      s.height = value;
      s.ctx.root.style.setProperty('--r5-h', `${value}px`);
      if (persist) s.ctx.setPref('r5.height', String(value));
      return value;
    },

    onKey(event) {
      const s = this.s;
      if (!s || !s.open) return;
      if (['Control', 'Shift', 'Alt', 'Meta', 'CapsLock'].includes(event.key)) return;
      const inside = s.ctx.root.contains(document.activeElement);
      const swallow = () => { event.preventDefault(); event.stopPropagation(); };
      // a list (Ctrl-b w, Ctrl-b ?) takes the keys while it is up
      if (s.pop && (inside || s.prefix)) {
        if (s.pop.kind === 'choose') {
          const ids = this.sorted().map((win) => win.id);
          const at = Math.max(0, ids.indexOf(s.pop.at));
          if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { s.pop.at = ids[(at + (event.key === 'ArrowDown' ? 1 : ids.length - 1)) % ids.length]; this.paint(); swallow(); return; }
          if (event.key === 'Enter') { const id = s.pop.at; s.pop = null; this.select(id, true); this.paint(); swallow(); return; }
          const digit = /^[0-9]$/.test(event.key) ? this.sorted().find((win) => win.index === Number(event.key)) : null;
          if (digit) { s.pop = null; this.select(digit.id, true); this.paint(); swallow(); return; }
        }
        s.pop = null;
        this.paint();
        swallow();
        return;
      }
      if (s.prefix) {
        swallow();
        s.prefix = false;
        const key = event.key;
        if (key === 'Escape') { this.paint(); return; }
        if (key === 'n') this.step(1);
        else if (key === 'p') this.step(-1);
        else if (key === 'l') { if (s.last && s.windows.has(s.last)) this.select(s.last, true); }
        else if (/^[0-9]$/.test(key)) { const win = this.sorted().find((item) => item.index === Number(key)); if (win) this.select(win.id, true); else this.say(`can't find window ${key}`); }
        else if (key === 'd') { this.detach(); return; }
        else if (key === 'w') s.pop = { kind: 'choose', at: s.selected };
        else if (key === '?') s.pop = { kind: 'keys' };
        else if (key === '&') this.close(s.selected);
        else if (event.ctrlKey && key.toLowerCase() === 'b') { if (s.selected) s.ctx.sendInput(s.selected, '\x02'); }
        this.paint();
        return;
      }
      if (event.ctrlKey && !event.altKey && !event.metaKey && !event.shiftKey && event.key.toLowerCase() === 'b' && inside) {
        swallow();
        s.prefix = true;
        this.paint();
      }
    },

    // ------------------------------------------------------------------------------------------ actions ----
    async stop() {
      const s = this.s;
      const id = s.selected;
      if (!id) return;
      s.stopping.add(id);
      this.paint();
      const answer = await s.ctx.interrupt(id);
      if (!answer.ok) {
        s.stopping.delete(id);
        this.say(`Stop was not sent: ${(answer.data && (answer.data.error || answer.data.refused)) || `HTTP ${answer.status}`}`);
      }
    },

    // WHY the message says what is not there yet: no tmux server exists behind this view (see the header comment)
    async copyAttach() {
      let ok = false;
      try { await navigator.clipboard.writeText(ATTACH); ok = true; } catch (_) { /* below */ }
      if (!ok) {
        const box = document.createElement('textarea');
        box.value = ATTACH;
        box.style.cssText = 'position:fixed;left:-1000px;top:0';
        document.body.appendChild(box);
        box.select();
        try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
        box.remove();
      }
      this.say(ok ? `copied: ${ATTACH} (answers once the host runs acts in tmux; this view is tmux-like only)` : `could not copy: ${ATTACH}`, 9000);
    },

    say(text, ms = 4000) {
      const s = this.s;
      s.message = text;
      s.messageAt = Date.now() + ms;
      this.paint();
    },

    // ------------------------------------------------------------------------------------------ drawing ----
    paint() {
      const s = this.s;
      if (!s) return;
      const h = s.ctx.h;
      const list = this.sorted();
      s.ctx.root.hidden = !s.open;
      s.el.toggle.setAttribute('aria-pressed', String(s.open));
      s.el.toggle.setAttribute('aria-expanded', String(s.open));
      s.el.detached.hidden = s.open || !list.length;
      const going = s.ctx.runs().filter((run) => run.active).length;
      s.el.badge.hidden = !going;
      s.el.badge.textContent = String(going);
      s.el.toggle.title = s.open ? `tmux session ${SESSION}: click to detach (Ctrl-b d)` : `tmux session ${SESSION}: click to attach`;
      // the window list
      const lastId = s.last && s.windows.has(s.last) ? s.last : null;
      const sig = list.map((win) => { const run = s.ctx.run(win.id) || {}; return `${win.id}|${win.index}|${run.state}|${win.id === s.selected}|${win.id === lastId}|${win.activity}|${win.ended}`; }).join('\n');
      if (sig !== s.statusSig) {
        s.statusSig = sig;
        s.el.wins.innerHTML = list.map((win) => {
          const run = s.ctx.run(win.id) || {};
          const current = win.id === s.selected;
          const flags = `${current ? '*' : win.id === lastId ? '-' : ''}${win.activity ? '#' : ''}${win.ended ? '!' : ''}`;
          const kind = { failed: 'error', stopped: 'warn', timed_out: 'warn' }[run.state];
          return `<button class="r5-win${run.active ? '' : ' dead'}" role="tab" aria-selected="${current}" tabindex="${current ? 0 : -1}" data-r5-win="${h.esc(win.id)}" title="${h.esc(run.label || '')} · ${h.esc(h.stateWords(run))}">`
            + `<span class="r5-name">${win.index}:${h.esc(nameOf(run))}</span><span class="r5-flag">${h.esc(flags)}</span>${!run.active && kind ? h.shape(kind, h.stateWords(run)) : ''}</button>`;
        }).join('');
      }
      const run = s.selected ? s.ctx.run(s.selected) : null;
      s.el.title.textContent = run ? `"${run.label}"` : '';
      s.el.title.title = run ? `${run.label} · ${h.stateWords(run)}${run.exit_code !== null && run.exit_code !== undefined ? ` · exit ${run.exit_code}` : ''}` : '';
      s.el.prefix.hidden = !s.prefix;
      const canStop = Boolean(run && run.active && run.interactive);
      s.el.stop.hidden = !canStop;
      if (canStop) {
        const moves = run.danger === 'moves';
        const stopping = s.stopping.has(run.id) || run.stopping;
        s.el.stop.className = `r5-btn r5-stop${moves ? ' moves' : ''}`;
        s.el.stop.disabled = stopping;
        s.el.stop.textContent = stopping ? 'Stopping…' : (moves ? 'Stop (motor goes limp)' : 'Stop');
        s.el.stop.title = moves ? 'Interrupt it, then the plugin\'s own stop sends the board mode 0 (it goes limp)' : 'Interrupt it (SIGINT, then SIGTERM, then SIGKILL); Ctrl-C in the window does the same';
      }
      if (s.message && Date.now() > s.messageAt) s.message = '';
      s.el.msg.hidden = !s.message;
      s.el.msg.textContent = s.message;
      s.el.msg.title = s.message;
      const clock = new Date().toTimeString().slice(0, 5);
      if (s.clockSig !== clock) { s.clockSig = clock; s.el.clock.textContent = clock; }
      // the panes: the current window shows; its terminal is made the first time it is shown
      s.el.empty.hidden = list.length > 0;
      if (!list.length) s.el.empty.innerHTML = `No windows in <code>${SESSION}</code>. A utility you run from the Utilities menu opens one here: <code>Ctrl-b ?</code> lists the keys.`;
      for (const win of list) {
        const on = win.id === s.selected;
        win.pane.hidden = !on;
        if (on && s.open && !win.bound) {
          win.bound = s.ctx.terminal(win.pane, win.id, { fontSize: 12 });
          requestAnimationFrame(() => win.bound && win.bound.fit());
        }
      }
      this.paintPop(list);
    },

    paintPop(list) {
      const s = this.s;
      const h = s.ctx.h;
      s.el.pop.hidden = !s.pop;
      if (!s.pop) { s.popSig = ''; return; }
      let html;
      if (s.pop.kind === 'keys') {
        html = `<div class="r5-pop-h">${SESSION} · keys (prefix Ctrl-b)</div>${KEYS.map(([key, what]) => `<div class="r5-pop-row"><span class="k">${h.esc(key)}</span><span class="t">${h.esc(what)}</span></div>`).join('')}`
          + `<div class="r5-pop-h">Finished windows stay until closed, as with remain-on-exit.</div>`;
      } else {
        html = `<div class="r5-pop-h">${SESSION} · choose a window (↑ ↓ Enter, 0-9)</div>${list.map((win) => {
          const run = s.ctx.run(win.id) || {};
          return `<div class="r5-pop-row" role="option" aria-selected="${win.id === s.pop.at}" data-r5-choose="${h.esc(win.id)}"><span class="k">${win.index}: ${h.esc(nameOf(run))}</span>`
            + `<span class="t">${h.esc(run.label || '')}</span><span class="m">${h.esc(h.stateWords(run))}</span></div>`;
        }).join('')}`;
      }
      if (s.popSig !== html) { s.popSig = html; s.el.pop.innerHTML = html; }
    },
  };

  window.IORunViews = window.IORunViews || {};
  window.IORunViews[ID] = view;
}());
