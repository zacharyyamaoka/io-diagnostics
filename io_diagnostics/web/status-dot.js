// The status dot before every row of the entity tree: ONE mapping from what the host says about a row to what is drawn, for every level
// (an adapter, a board, a camera, a composite) alike.
//
//   window.IOStatusDot = { visual(row), html(row) }
//
//   hollow ring   stale: known, but not heard lately (presence.state "stale")
//   solid dot     connected and live (presence.state "heard" or "live")
//   pulsing       it is doing something now (row.activity, docs/api-v1.md "activity"): a utility running on it, a console or a tool
//                 holding it, its first look after a plug-in. Pulsing is added to either shape, never a shape of its own.
//   no dot        a row with nothing to hear from (presence null: a port, a folder), or a state this page does not know
//
// WHY (Zach, 2026-11-10): "Hollow ring: stale or not heard. Solid dot: connected and live, like a plugged-in port versus an empty one.
// Pulsing: the entity is actively doing something ... A merely-connected idle entity must NOT pulse. Routine background state polling
// does not count." So "alive" (presence heard or live, a follower streaming, a presence pass answering) is never what pulses: only the
// host's `activity` is, and the host sets that from real signals only (runs, consoles, holders, the on-connect look).
//
// Loads in the page (window.IOStatusDot) and in Node (module.exports) so tests/test_status_dot.js can walk every state.
'use strict';
(function (root) {
  const SHAPES = { heard: 'solid', live: 'solid', stale: 'hollow' };
  const esc = (text) => String(text ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // activity counts only as the object the host sends ({kind, text}); anything else (true, a string, {}) is idle
  const doing = (activity) => (activity && typeof activity === 'object' && typeof activity.kind === 'string' && activity.kind ? activity : null);

  function visual(row) {
    const presence = (row && row.presence) || null;
    const shape = (presence && SHAPES[presence.state]) || 'none';
    if (shape === 'none') return { shape, pulse: false, title: '' };
    const activity = doing(row.activity);
    const base = shape === 'hollow' ? presence.text || 'Not heard from lately' : 'Connected';
    return { shape, pulse: Boolean(activity), title: activity ? `${base} · ${activity.text || activity.kind}` : base };
  }

  function html(row) {
    const dot = visual(row);
    if (dot.shape === 'none') return '<span class="t6-pres t6-pres-none" aria-hidden="true"></span>';
    return `<span class="t6-pres t6-pres-${dot.shape}${dot.pulse ? ' t6-pres-pulse' : ''}" role="img" aria-label="${esc(dot.title)}" title="${esc(dot.title)}"></span>`;
  }

  const IOStatusDot = { visual, html };
  root.IOStatusDot = IOStatusDot;
  if (typeof module !== 'undefined' && module.exports) module.exports = IOStatusDot;
})(typeof window !== 'undefined' ? window : globalThis);
