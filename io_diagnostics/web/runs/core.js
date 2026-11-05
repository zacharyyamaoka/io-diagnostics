// The runs runtime (docs/runs-v1.md section 7): window.IORuns. It keeps the page's copy of the runs, follows a run's
// output stream, sends the page's three writes, and binds a run to an xterm.js terminal. The five proposals
// (runs/r1.js .. r5.js, window.IORunViews) draw against it through the ctx app.js builds; none of them calls the API itself.
'use strict';
(function () {
  const HEADERS = { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' };
  const runsById = new Map();   // id -> run summary (the newest copy; `_at` is when this page last heard of it)
  const listeners = new Set();
  const details = new Map();    // id -> the run with its result, once it has ended
  let changed = 0;

  // ---------------------------------------------------------------------------------------------- the store ----
  // WHY the signature: the list is polled every second with a moving elapsed_s; a listener hears about a change only
  // when something a view draws as a fact changed (state, exit code, stop, who may type), never for the timer.
  const signature = (run) => [run.id, run.state, run.active, run.stopping, run.exit_code, run.interactive, run.label, run.cols, run.rows].join('|');

  function ingest(runs) {
    const items = runs && Array.isArray(runs.items) ? runs.items : Array.isArray(runs) ? runs : [];
    let touched = false;
    const now = Date.now();
    for (const run of items) {
      const before = runsById.get(run.id);
      runsById.set(run.id, { ...(before && !run.result ? { result: before.result, tail: before.tail } : {}), ...run, _at: now });
      if (!before || signature(before) !== signature(run)) touched = true;
    }
    if (touched) notify();
    return touched;
  }
  function notify() {
    changed += 1;
    for (const listener of [...listeners]) {
      try { listener(); } catch (error) { console.error('runs listener:', error); }
    }
  }
  const list = () => [...runsById.values()].sort((a, b) => (a.id < b.id ? 1 : a.id > b.id ? -1 : 0));
  const get = (id) => runsById.get(id) || null;
  function onChange(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  // One run with its result (GET /api/v1/runs/<id>); the store learns it too.
  async function detail(id) {
    const response = await fetch(`/api/v1/runs/${encodeURIComponent(id)}`, { cache: 'no-store', signal: AbortSignal.timeout(8000) });
    const run = await response.json();
    if (!response.ok || run.error) throw new Error(run.error || `HTTP ${response.status}`);
    details.set(id, run);
    const before = runsById.get(id);
    runsById.set(id, { ...run, _at: Date.now() });
    if (!before || signature(before) !== signature(run)) notify();
    return run;
  }
  async function refresh(id) {
    try { return await detail(id); } catch (_) { return null; }
  }

  // --------------------------------------------------------------------------------------------- the writes ----
  async function write(id, op, body) {
    try {
      const response = await fetch(`/api/v1/runs/${encodeURIComponent(id)}/${op}`, { method: 'POST', headers: HEADERS, body: JSON.stringify(body) });
      const data = await response.json().catch(() => ({}));
      return { ok: response.ok, status: response.status, data };
    } catch (error) {
      return { ok: false, status: 0, data: { error: `the host did not answer: ${error}` } };
    }
  }
  const sendInput = (id, text) => write(id, 'input', { text });
  const interrupt = (id) => write(id, 'signal', { signal: 'interrupt' });
  const resize = (id, cols, rows) => write(id, 'resize', { cols, rows });
  const castUrl = (id) => `/api/v1/runs/${encodeURIComponent(id)}/cast`;

  // ---------------------------------------------------------------------------------------------- the stream ----
  // SSE read with fetch (not EventSource: that reconnects after `end` by itself); a dropped connection resumes from the
  // last byte offset; `end` is final.
  function bytesOf(b64) {
    const text = atob(b64);
    const out = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i += 1) out[i] = text.charCodeAt(i);
    return out;
  }
  function stream(id, onData, onEnd) {
    let stopped = false;
    let ended = false;
    let offset = null;
    let controller = null;
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    function handle(block) {
      let name = 'message';
      const data = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) name = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (!data.length) return;
      const payload = JSON.parse(data.join('\n'));
      if (name === 'output') {
        const bytes = bytesOf(payload.b64);
        offset = payload.offset + bytes.length;
        if (!stopped) onData(bytes, { offset: payload.offset, t: payload.t });
      } else if (name === 'hello') {
        if (offset === null) offset = payload.from;
      } else if (name === 'end') {
        ended = true;
        runsById.set(payload.run.id, { ...payload.run, _at: Date.now() });
        details.set(payload.run.id, payload.run);
        notify();
        if (!stopped) onEnd(payload.run);
      }
    }
    (async () => {
      let backoff = 250;
      while (!stopped && !ended) {
        controller = new AbortController();
        try {
          const response = await fetch(`/api/v1/runs/${encodeURIComponent(id)}/stream${offset === null ? '' : `?from=${offset}`}`, { signal: controller.signal, cache: 'no-store' });
          if (response.status === 404) { ended = true; if (!stopped) onEnd(null); return; }
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = '';
          backoff = 250;
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let cut = buffer.indexOf('\n\n');
            while (cut >= 0) {
              handle(buffer.slice(0, cut));
              buffer = buffer.slice(cut + 2);
              cut = buffer.indexOf('\n\n');
            }
          }
        } catch (error) {
          if (stopped) return;
        }
        if (!stopped && !ended) { await sleep(backoff); backoff = Math.min(backoff * 2, 3000); }
      }
    })();
    return () => { stopped = true; if (controller) controller.abort(); };
  }

  // ---------------------------------------------------------------------------------------------- the words ----
  const STATE_WORDS = { queued: 'queued', waiting: 'waiting for the bus', running: 'running', done: 'done', failed: 'failed', stopped: 'stopped', timed_out: 'timed out' };
  function stateWords(run) {
    if (!run) return '';
    if (run.active && run.stopping) return 'stopping';
    return STATE_WORDS[run.state] || run.state;
  }
  // seconds as 0:12 / 1:05:09; an active run keeps counting from the last time this page heard its elapsed_s
  function elapsed(run) {
    if (!run) return '';
    const seconds = Math.max(0, Math.floor((run.elapsed_s || 0) + (run.active && run._at ? (Date.now() - run._at) / 1000 : 0)));
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
  }
  const active = () => list().filter((run) => run.active);

  // --------------------------------------------------------------------------------------------- the terminal ----
  // The page's colours resolved by the browser (a token may be a system colour keyword), so the terminal follows the skin
  // and light / dark. Computed with a probe element: getComputedStyle turns any CSS colour into rgb().
  function tokenColours() {
    const probe = document.createElement('span');
    probe.style.cssText = 'position:absolute;visibility:hidden;color:var(--io-text);background-color:var(--io-surface);border-color:var(--io-selected)';
    document.body.appendChild(probe);
    const style = getComputedStyle(probe);
    const out = { foreground: style.color, background: style.backgroundColor, selection: style.borderTopColor };
    probe.remove();
    return out;
  }
  const PALETTE = {
    light: { black: '#1f2328', red: '#b3261e', green: '#1a7f37', yellow: '#8a5a00', blue: '#0b5cad', magenta: '#8250df', cyan: '#0a7b83', white: '#6e7781',
      brightBlack: '#57606a', brightRed: '#cf222e', brightGreen: '#2da44e', brightYellow: '#9a6700', brightBlue: '#1f6feb', brightMagenta: '#a475f9', brightCyan: '#1b9aa3', brightWhite: '#8c959f' },
    dark: { black: '#484f58', red: '#ff7b72', green: '#7ee787', yellow: '#e3b341', blue: '#79c0ff', magenta: '#d2a8ff', cyan: '#56d4dd', white: '#b1bac4',
      brightBlack: '#6e7681', brightRed: '#ffa198', brightGreen: '#56d364', brightYellow: '#f2cc60', brightBlue: '#a5d6ff', brightMagenta: '#e2c5ff', brightCyan: '#b3f0ff', brightWhite: '#f0f6fc' },
  };
  function terminalTheme() {
    const mode = document.documentElement.dataset.mode === 'dark' ? 'dark' : 'light';
    const colours = tokenColours();
    return { ...PALETTE[mode], foreground: colours.foreground, background: colours.background, cursor: colours.foreground, cursorAccent: colours.background,
      selectionBackground: colours.selection };
  }

  // Bind a run to an xterm.js terminal inside `el`: replays and follows its output, forwards typing while the run is active
  // and interactive, keeps the terminal's size and the run's in step. -> {term, fit(), focus(), dispose()}
  function attachTerminal(el, id, opts = {}) {
    const Terminal = window.Terminal;
    const FitAddon = window.FitAddon && window.FitAddon.FitAddon;
    if (!Terminal) {
      el.textContent = 'The terminal emulator (vendor/xterm/xterm.js) did not load.';
      return { term: null, fit() {}, focus() {}, dispose() {} };
    }
    const term = new Terminal({ fontSize: opts.fontSize || 12, fontFamily: getComputedStyle(document.documentElement).getPropertyValue('--io-font-mono') || 'monospace',
      scrollback: opts.scrollback ?? 5000, cursorBlink: true, theme: terminalTheme(), allowProposedApi: true, convertEol: false });
    const fitter = FitAddon ? new FitAddon() : null;
    if (fitter) term.loadAddon(fitter);
    term.open(el);
    let disposed = false;
    let lastSize = '';
    const writable = () => { const run = get(id); return Boolean(run && run.active && run.interactive && !opts.readOnly); };
    // WHY the run must be running to be sized: a queued run (a batch of utilities waits on its bus group) has no terminal yet and the host
    // answers 409; the size is sent when it starts (the store's change below), never as a failed request
    const sizeable = () => { const run = get(id); return Boolean(writable() && run && (run.state === 'running' || run.state === 'waiting')); };
    let unsent = false;
    function fit() {
      if (disposed || !fitter || !el.offsetWidth || !el.offsetHeight) return;
      try { fitter.fit(); } catch (_) { return; }
      const size = `${term.cols}x${term.rows}`;
      if (size !== lastSize || unsent) {
        lastSize = size;
        if (sizeable()) { unsent = false; resize(id, term.cols, term.rows); } else unsent = writable();
      }
    }
    const sizeWhenRunning = onChange(() => { if (unsent && sizeable()) fit(); });
    term.onData((text) => { if (writable()) sendInput(id, text); });
    // WHY Escape and the page's own shortcuts stay with the process: a terminal program (vi, less) needs them
    term.attachCustomKeyEventHandler((event) => !(event.ctrlKey && event.key === '`'));
    const observer = new ResizeObserver(() => fit());
    observer.observe(el);
    const theme = new MutationObserver(() => { term.options.theme = terminalTheme(); });
    theme.observe(document.documentElement, { attributes: true, attributeFilter: ['data-mode', 'data-skin'] });
    const stop = stream(id, (bytes) => term.write(bytes), () => { lastSize = ''; });
    requestAnimationFrame(fit);
    return {
      term,
      fit,
      focus() { if (!disposed) term.focus(); },
      dispose() {
        disposed = true;
        stop();
        sizeWhenRunning();
        observer.disconnect();
        theme.disconnect();
        term.dispose();
      },
    };
  }

  window.IORuns = { ingest, list, get, onChange, detail, refresh, sendInput, interrupt, resize, castUrl, stream, attachTerminal, stateWords, elapsed, active,
    revision: () => changed };
  window.IORunViews = window.IORunViews || {};
}());
