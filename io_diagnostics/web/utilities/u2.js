// Utility menu u2 · STUB (a copy of u0: a proposal lane replaces this file) (round 3 baseline; contract: docs/round2-seams.md §6).
// One row per utility: its icon and title, nothing else at a glance; the description is a hover / focus tooltip
// (Zach, 2026-10-23: "too much information at a glance ... the description is maybe a tool tip"). A busy utility
// greys out with one quiet line under its title ("Checking communication… 3 s"); a blocked one says why in muted
// text. Danger is a neutral word, never a warn / error colour (those mean a diagnostic). The page owns the flow: a
// [data-util] click is a dry run, ctx.panel() is its panel, anything but a query runs from the confirm modal.
'use strict';
(function (root) {
  root.IOUtilityMenus = root.IOUtilityMenus || {};
  root.IOUtilityMenus.u2 = {
    name: 'Stub (= u0)',
    description: 'Icon + title per utility, the description as a tooltip, a greyed busy row with a quiet line under it, danger as a neutral word.',
    order: 2,
    html(ctx) {
      const { esc, utilIcon } = ctx.h;
      const row = (action) => {
        const busy = ctx.busy(action);
        const waiting = ctx.waiting(action);
        const runnable = ctx.runnable(action);
        const tip = `u-tip-${esc(action.name)}`;
        // blocked: the reason on one muted line (its full words in the tooltip)
        const reason = !busy && action.state !== 'ready' && action.reason ? `<span class="u-reason">${esc(action.reason)}</span>` : '';
        const note = busy ? `<span class="u-busy">${esc(busy)}</span>` : reason;
        const danger = action.danger !== 'query' ? `<span class="danger ${esc(action.danger)}">${esc(action.danger)}</span>` : '';
        const tipText = [action.description, waiting ? 'Type the new CAN id (1-126) first.' : '', action.state !== 'ready' && action.reason ? action.reason : '']
          .filter(Boolean).join('\n');
        const button = `<button class="u-btn" data-util="${esc(action.name)}"${waiting || busy ? ' disabled' : ''} aria-describedby="${tip}">`
          + `${utilIcon(action.name)}<span class="u-text"><span class="u-title">${esc(action.title)}</span>${note}</span>${danger}`
          + `<span class="u-desc" id="${tip}" role="tooltip">${esc(tipText)}</span></button>`;
        return `<div class="urow${ctx.isActive(action) ? ' active' : ''}${runnable ? '' : ' blocked'}${busy ? ' busy' : ''}" data-util-row="${esc(action.name)}">`
          + `${ctx.needsId(action) ? `<div class="u-line">${button}${ctx.newIdBox(action)}</div>` : button}${ctx.panel(action)}</div>`;
      };
      return ctx.groups.map((group) => `<div class="ugroup ugroup-${esc(group.id)}"><div class="ugroup-h">${esc(group.title)}</div>`
        + `${group.items.map(row).join('')}</div>`).join('');
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
