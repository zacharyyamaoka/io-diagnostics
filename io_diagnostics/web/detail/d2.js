// Detail layout d2 · STUB (a copy of d0: a proposal lane replaces this file) (round 3 baseline; contract: docs/round2-seams.md §6).
// The entity's detail as one column of collapsible sections, read top to bottom (Zach, 2026-10-23: "the eye easily
// reads top to bottom not in these grid patterns"), compact width. The page owns the data and the flows; this file
// only chooses which sections show, in which order, with which titles.
'use strict';
(function (root) {
  root.IODetailLayouts = root.IODetailLayouts || {};
  root.IODetailLayouts.d2 = {
    name: 'Stub (= d0)',
    description: 'Sections with collapsible headers, one column: what to check, utilities, expected setup, status, identity, connection, parts, checks, faults, link health, events.',
    order: 2,
    // container = #detail; detail = GET /api/v1/node for the selection (null while it loads, or for a grouping row);
    // ctx = the page's building blocks (docs/round2-seams.md §6). Called every second: ctx.draw() skips an unchanged page.
    render(container, detail, ctx) {
      const p = ctx.parts;
      const s = ctx.section;
      if (ctx.group) {
        ctx.draw(`${p.head()}<div class="d-body">${s('members', `Members · ${ctx.counts.members}`, p.members())}`
          + `${s('links', 'Link health', p.links(), { right: 'the computer diagnoses its own wires' })}</div>`);
        return;
      }
      if (!detail) {
        ctx.draw(`${p.head()}${p.banners()}<div class="d-body"><div class="muted">loading…</div></div>`);
        return;
      }
      const owner = (detail.actions[0] || {}).owner || '';
      ctx.draw(`${p.head()}${p.banners()}<div class="d-body">`
        + s('why', 'What to check', p.whatToCheck())
        + s('utilities', 'Utilities', p.utilities(), { right: `${ctx.h.esc(owner)} runs them · every click is a dry run first` })
        + s('setup', 'Expected setup', p.setup(), { right: 'the rig file, never the device' })
        + s('status', 'Status', p.status())
        + s('identity', 'Identity', p.identity())
        + s('connection', `Connection (folded) · ${ctx.counts.connection}`, p.connection(), { right: 'the layers this row is reached through, folded into it' })
        + s('parts', `Parts · ${ctx.counts.parts}`, p.parts(), { right: 'composition (part_of), folded into this row' })
        + s('checks', `Checks · ${ctx.counts.checks}`, p.checks())
        + s('faults', 'Faults', p.faults())
        + s('links', 'Link health', p.links(), { right: 'the computer diagnoses its own wires' })
        + s('events', 'Events · this row and below', p.events())
        + '</div>');
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
