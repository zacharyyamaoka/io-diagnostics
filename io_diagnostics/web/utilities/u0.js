// Utility menu u0 · Quiet list (round 3 baseline; contract: docs/round2-seams.md §6).
// One row per utility: its icon and title, nothing else at a glance; the description is a hover / focus tooltip
// (Zach, 2026-10-23: "too much information at a glance ... the description is maybe a tool tip"). Danger is a neutral
// word, never a warn / error colour (those mean a diagnostic). The page owns the flow: a [data-util] click runs an
// immediate utility at once and opens any other's Run page, exactly as the Utilities tab's button does.
// WHY two states (Zach, 2026-10-29): "able to run: just the name of the utility; OR not able to run: greyed out with an
// explanation underneath as to why." A row that is starting or running is not able to run either: greyed, its quiet line.
'use strict';
(function (root) {
  root.IOUtilityMenus = root.IOUtilityMenus || {};
  root.IOUtilityMenus.u0 = {
    name: 'Quiet list',
    description: 'Icon + title per utility, the description as a tooltip; a utility that cannot run now is greyed with one line of why.',
    order: 0,
    html(ctx) {
      const { esc, utilIcon, utilLabel, reasonText } = ctx.h;
      const row = (action) => {
        const busy = ctx.busy(action);
        const runnable = ctx.runnable(action) && !busy;
        const tip = `u-tip-${esc(action.name)}`;
        const why = busy || (runnable ? '' : reasonText(action));
        const note = why ? `<span class="${busy ? 'u-busy' : 'u-reason'}">${esc(why)}</span>` : '';
        const danger = action.danger !== 'query' ? `<span class="danger ${esc(action.danger)}">${esc(action.danger)}</span>` : '';
        const tipText = [action.description, why].filter(Boolean).join('\n');
        const button = `<button class="u-btn" data-util="${esc(action.name)}"${runnable ? '' : ' disabled'} aria-describedby="${tip}" role="menuitem">`
          + `${utilIcon(action.name)}<span class="u-text"><span class="u-title">${esc(utilLabel(action))}</span>${note}</span>${danger}`
          + `<span class="u-desc" id="${tip}" role="tooltip">${esc(tipText)}</span></button>`;
        return `<div class="urow${ctx.isActive(action) ? ' active' : ''}${runnable ? '' : ' blocked'}${busy ? ' busy' : ''}" data-util-row="${esc(action.name)}">`
          + `${button}</div>`;
      };
      return ctx.groups.map((group) => `<div class="ugroup ugroup-${esc(group.id)}"><div class="ugroup-h">${esc(group.title)}</div>`
        + `${group.items.map(row).join('')}</div>`).join('');
    },
  };
})(typeof window !== 'undefined' ? window : globalThis);
