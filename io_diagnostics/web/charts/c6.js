// Chart view c6 · Unfold, focus, pin (the integrated pick; contract: charts/core.js). Zach, 2026-10-24: c2 "Unfold in place" is the
// default for every value sparkline; "I like the focus panel as well and I think you can support both! one is like a drop down where
// one is like a zoom/expand button"; and c4's Dashboard ("I really really like the dashboard concept ... lets also include that").
// This view is not a fourth chart: it is c2, c5 and c4 mounted together, each unchanged, and a row's controls routed to them:
//   the sparkline itself and the chevron   c2   unfold a chart right under the row (press its header to fold it)
//   the expand button                      c5   a page of its own: zoom, pan, health timeline, CSV (Back returns)
//   the pin button                         c4   a tile on the Dashboard view, saved with the app's settings
// WHY composed, not copied: each of the three keeps its file, its tests (tests/test_charts.js drives them one by one) and its
// state; the lane designing richer dashboards replaces c4 alone. `parts` is how core.js's env.isActive(id) knows they are live.
'use strict';
(function (root) {
  const ID = 'c6';
  const PARTS = ['c2', 'c5', 'c4'];
  const part = (id) => (root.IOChartViews || {})[id];
  const live = () => PARTS.map(part).filter(Boolean);

  const each = (method, ...args) => { for (const view of live()) if (view[method]) view[method](...args); };

  root.IOChartViews = root.IOChartViews || {};
  root.IOChartViews[ID] = {
    name: 'Unfold, focus, pin',
    description: 'Press a sparkline: its row unfolds into a chart (c2). The expand button opens the value on a page of its own with zoom and pan (c5); the pin adds it to your Dashboard (c4).',
    order: 6,
    parts: PARTS,
    mount(env) { each('mount', env); },
    unmount(env) { each('unmount', env); },
    attrs: (spec, key) => live().map((view) => (view.attrs ? view.attrs(spec, key) : '')).join(''),
    tip: (spec, key) => part('c2').tip(spec, key),
    // left to right: pin, expand, unfold (the chevron stays where c2 alone puts it, at the right edge)
    buttons: (spec, key) => [
      { name: 'pin', icon: 'pin', tip: part('c4').tip(spec, key), on: part('c4').btnOn(spec, key) },
      { name: 'focus', icon: 'open', tip: part('c5').tip(spec, key), on: false },
      { name: 'unfold', icon: 'chevron', tip: part('c2').tip(spec, key), on: false },
    ],
    on(kind, event, hit) {
      if (kind !== 'click') return PARTS.map(part).some((view) => view && view.on && view.on(kind, event, hit));
      // every click lets c4 close its open "Add from this page" menu; only the pin button also toggles a pin
      if (hit.button !== 'pin') part('c4').on(kind, event, { ...hit, el: null, spec: null });
      if (hit.button === 'pin') return part('c4').on(kind, event, hit);
      if (hit.button === 'focus') return part('c5').on(kind, event, hit);
      return part('c2').on(kind, event, hit);
    },
    update(env) { each('update', env); },
    sync(env) { each('sync', env); },
  };
})(typeof window !== 'undefined' ? window : globalThis);
