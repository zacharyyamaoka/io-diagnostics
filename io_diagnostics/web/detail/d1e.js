// Detail layout d1e · Checklist. The Diagnostics section as a pre-flight checklist: plain-language lines ("Firmware is the
// latest (1.1.2)", "Board answers its queries"), a problem first, bold and open inline with its numbered what-to-check, the
// OK lines quiet and dimmed (one click opens one). A health spark appears only on a line whose history held a problem in
// the window, so a calm entity stays calm. The status line at the top drops down into one timeline lane per diagnostic.
// WHY generated wording: a diagnostic's name and message are the authored values; the line is a reading of them from a
// small phrasebook (by the part of the name after the colon), falls back to "<what> — <message>", and the authored name
// and message are one click away and in the line's hover (truthful rendering). Base: detail/d1x.js; chart: detail/health.js.
'use strict';
(function (root) {
  const X = () => root.IODiagX;
  const CSS = `
.de-list { display: grid; margin: 6px 0 4px; }
.de-r { border-bottom: 1px solid var(--io-border-soft); }
.de-line { display: grid; grid-template-columns: 18px minmax(0, 1fr) var(--de-spark); gap: 10px; align-items: baseline; padding: 6px 4px 6px calc(4px + var(--depth, 0) * 14px); border-radius: var(--io-radius-sm); cursor: pointer; font-size: 13px; }
.de-line:hover { background: var(--io-hover); }
.de-line:focus-visible { outline: var(--io-focus-width) solid var(--io-focus); outline-offset: 1px; }
.de-mark { display: inline-flex; justify-content: center; align-self: center; color: var(--io-faint); font-size: 13px; line-height: 1; }
.de-mark .shape { width: 14px; height: 14px; }
.de-t { min-width: 0; overflow-wrap: anywhere; }
.de-t .ro-star, .de-t .ro-grip { display: inline-block; vertical-align: middle; }
.de-r.ok .de-t { color: var(--io-faint); }
.de-r.bad .de-t { color: var(--io-text-strong); font-weight: 600; }
.de-h { align-self: center; min-width: 0; }
.de-r .dx-x { padding-left: 32px; }
.de-r .de-orig { color: var(--io-faint); font-size: 11.5px; }
`;
  const SPARK_W = 96;

  const LEVEL_WORDS = { warn: 'needs a look', error: 'has failed', stale: 'has gone quiet', unknown: 'cannot be checked right now' };
  const sentence = (text) => String(text).replace(/^\s*(.)/, (m, c) => c.toUpperCase());
  const parts = (name) => { const at = String(name).indexOf(': '); return at > 0 ? { who: name.slice(0, at), what: name.slice(at + 2) } : { who: '', what: String(name) }; };

  // The wording: [pattern on the part after the colon, ok(who, message), bad(who, message, level)]; no pattern hit reads
  // "<what> is fine — <message>" (OK) or "<what> <needs a look>: <message>" (a problem)
  const PHRASES = [
    [/^comms$/i, (who, m) => `${who || 'It'} answers its queries (${m})`, (who, m, level) => `${who || 'It'} ${LEVEL_WORDS[level]}: ${m}`],
    [/^firmware$/i, (who, m) => { const v = /\(latest ([^)]+)\)/.exec(m); return v ? `Firmware is the latest (${v[1]})` : `Firmware: ${m}`; }, (who, m) => `Firmware is not up to date: ${m}`],
    [/^presence$/i, (who, m) => `Plugged in ${m}`, (who, m) => `Connection problem: ${m}`],
    [/^plugin run$/i, (who, m) => `The check itself ran fine (${m})`, (who, m) => `The check itself did not run properly: ${m}`],
    [/^camera$/i, (who, m) => `Camera: ${m}`, (who, m) => `Camera: ${m}`],
  ];
  function plain(row) {
    const { who, what } = parts(row.name);
    const message = row.message || 'no message';
    const hit = PHRASES.find(([pattern]) => pattern.test(what));
    const text = hit ? (row.bad ? hit[2](who, message, row.level) : hit[1](who, message))
      : row.bad ? `${what} ${LEVEL_WORDS[row.level]}: ${message}` : `${what} is fine — ${message}`;
    return who && text.startsWith(who) ? text : sentence(text);   // never upper-case a device name ("fdcanusb ...")
  }

  function section(node, detail, ctx) {
    const x = X();
    return x.section(node, detail, ctx, (m, hist) => {
      const lines = x.walk(node, x.planOf(node, m.rows), (r) => {
        const row = r.row;
        const open = x.isOpen(`row:${row.key}`, row.bad);
        const status = hist.byName.get(row.name);
        const flaky = Boolean(status && status.levels.some((span) => span.level && span.level !== 'ok')); // a problem at some point in the window
        const spark = flaky ? x.cellsOf(row, hist, SPARK_W, 'spark', ctx) : '';
        return `<div class="de-r ${row.bad ? 'bad' : 'ok'}" style="--depth:${r.depth}"${r.drag}${row.bad ? ' data-diagnostic' : ''}>`
          + `<div class="de-line" data-dx-toggle="row:${x.esc(row.key)}"${row.bad ? ' data-dx-default-open' : ''} role="button" tabindex="0" aria-expanded="${open}" title="${x.esc(row.name)} — ${x.esc(row.message)}">`
          + `<span class="de-mark">${row.bad ? ctx.h.shape(row.level, row.level) : '✓'}</span><span class="de-t">${r.grip}${r.star}${x.esc(plain(row))}</span><span class="de-h">${spark}</span></div>`
          + `${open ? `<div class="dx-x"><div class="de-orig">${x.esc(row.name)}</div>${x.body(row, ctx, { bare: true })}</div>` : ''}</div>`;
      });
      return `${x.summary(node, m, ctx, hist)}${x.toolbar(node)}<div class="de-list" style="--de-spark:${SPARK_W}px">${lines}</div>`;
    }, (m) => (m.problems.length ? `${m.ok.length} of ${m.rows.length} pass · the rest first` : `all ${m.rows.length} pass`));
  }

  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d1e = {
    name: 'Checklist',
    description: 'A pre-flight checklist: plain-language lines, a problem first and open inline with its numbered what-to-check, the OK lines dimmed. A health spark only on a line whose history held a problem.',
    order: 1.5,
    diagnostics: (node, detail, ctx) => section(node, detail, ctx),
    render(container, detail, ctx) { X().page(container, detail, ctx, 'd1e', section, CSS); },
  };
})(typeof window !== 'undefined' ? window : globalThis);
