/* Add to dashboard: the host's half on /dx/ (data-ui CONTRACT §34, PEP 0014; docs/peps/0001-robotdx-on-data-ui.md §17).

   Zach, 2026-10-29: "Dashboards are built with data-ui. Humans curate, AI builds." A reader adds what they are looking at to a
   dashboard of theirs, "like the amazon thing of adding to a saved list", and it lands at the bottom, to be arranged later.
   data-ui offers the item (a view's or a panel's ⋯, a KPI card, a legend series, a peek field), opens the picker and hands the
   page the panel as it was seen; data-ui never writes a file. This page lists the dashboards and saves:

   - dashboards(), on #dx-app (the picker looks on the view and up): the host's files (GET /api/v1/dashboards, newest first) or,
     on an older host, this browser's, each with its text. A text is read once per version: the list's etag says when it changed.
   - data-add-to-dashboard: one PUT of the whole text with If-Match, the etag of the text the panel was appended to. A 412 means
     another tab or person saved since: read it again, append again, once; a second 412 says so and keeps theirs. New dashboard…
     creates with If-None-Match: *, so a name already there is refused, never written over. "Added to <name>" links the dashboard
     on this page; a dashboard open here is drawn again (boards.js, dx-board-saved).

   WHY the page appends the panel itself (data-ui's exported appendPanel) rather than saving the text data-ui made: the append goes
   on the text as the host holds it now (a 412 reads it again), and the data path is written against where the file lives. The
   panel is data-ui's own: in a dashboard it names the file that dashboard declares for its dataset (CONTRACT §34's
   dashboardPanel.file; openBase sets the dashboard's baseUrl), which on this page is the binding (config.json?path=<entity>), not
   the shared dataset's url (PEP §12.1: one dataset per kind, whose url is whichever binding made it first).
   WHY base = dashboards/<name>.base: every dashboard this page writes names its data relative to the folder it opens dashboards
   from (app.js BOARDS), as the starters do, so appendPanel finds a dataset the dashboard already reads by its file. */

import { appendPanel } from '../vendor/data-ui/data-ui.js';
import { storeOf, nameFor } from './boards.js';

const why = (r) => (r.tooLarge ? 'the text would be over the host\'s 1 MB' : r.error || 'the host refused it');

/** Install the picker's list and the save on `app` (the element every view of the page is inside). */
export function installAddToDashboard(app, { BOARDS, go }) {
  const boardUrl = (id) => new URL(`${encodeURIComponent(id)}.base`, BOARDS).href;
  const texts = new Map();   // id -> {text, etag}: each dashboard's text as last read or written here

  async function reread(s, id) {
    const r = await s.read(id);
    if (!r) { texts.delete(id); return null; }
    const kept = { text: r.text, etag: r.etag };
    texts.set(id, kept);
    return kept;
  }

  app.dashboards = async () => {
    const s = await storeOf(BOARDS);
    const list = await s.list();
    // a file read since it last changed is not read again; one that cannot be read (not UTF-8) is left out of the list
    await Promise.all(list.map((b) => { const kept = texts.get(b.id); return kept && b.etag && kept.etag === b.etag ? null : reread(s, b.id).catch(() => null); }));
    return list.filter((b) => texts.has(b.id)).map((b) => ({ id: b.id, title: b.title || b.id, url: boardUrl(b.id), text: texts.get(b.id).text }));
  };

  app.addEventListener('data-add-to-dashboard', (e) => {
    const { dashboard, panel } = e.detail;
    e.detail.respondWith(add(dashboard, panel));
  });

  async function add(dashboard, panel) {
    const s = await storeOf(BOARDS);
    if (dashboard.new) return create(s, dashboard.title, panel);
    const id = String(dashboard.id);
    let kept = texts.get(id) || await reread(s, id);
    for (let tries = 0; ; tries += 1) {
      if (!kept) return { ok: false, reason: `${id} is no longer there` };
      const text = appendPanel(kept.text, panel, { base: boardUrl(id) });
      const r = await s.write(id, text, kept.etag);
      if (r.ok) return saved(id, dashboard.title || id, text, r.etag);
      if (!r.conflict) return { ok: false, reason: why(r) };
      // WHY once: another tab or person saved since this page read it; theirs is kept and the panel goes under it. A second
      // refusal means it is being edited now, and the reader decides (as the board's own conflict note does, boards.js)
      if (tries >= 1) return { ok: false, reason: `${id} changed while you were adding; reload it and add again` };
      kept = await reread(s, id);
    }
  }

  async function create(s, title, panel) {
    if (s.kind !== 'host') {
      // this browser's store (an older host): it names its own dashboards
      const text = appendPanel('', panel, { title, base: boardUrl('new') });
      const id = await s.create({ title, text });
      return saved(id, title, text, null);
    }
    const name = nameFor(title);
    if (s.starters.has(name.toLowerCase())) return { ok: false, reason: `“${name}” is a starter's name: give yours another` };
    const text = appendPanel('', panel, { title, base: boardUrl(name) });
    const r = await s.write(name, text, null, { create: true });
    if (r.ok) return saved(name, name, text, r.etag);
    if (r.conflict) return { ok: false, reason: `there is already a dashboard “${name}”: pick it in the list, or give this one another name` };
    return { ok: false, reason: why(r) };
  }

  function saved(id, title, text, etag) {
    texts.set(id, { text, etag });
    dispatchEvent(new CustomEvent('dx-board-saved', { detail: { id, text, etag } }));
    return { ok: true, title, href: `${location.pathname}?${new URLSearchParams({ go: 'dashboards', board: id })}` };
  }

  // the picker's "Open ↗" goes to the dashboard in this page (a history entry; the clock and what the page holds are kept)
  app.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('.dv-addpick-status a[href]');
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const u = new URL(a.href);
    if (u.origin !== location.origin || u.pathname !== location.pathname || u.searchParams.get('go') !== 'dashboards') return;
    e.preventDefault();
    const close = a.closest('.dv-addpick') && a.closest('.dv-addpick').querySelector('.dv-addpick-x');
    if (close) close.click();
    go('dashboards', { board: u.searchParams.get('board') || '' });
  });
}
