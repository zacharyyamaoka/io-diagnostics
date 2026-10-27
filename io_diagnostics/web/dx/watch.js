/* The State heartbeat: the page's ONE write (docs/peps/0001-robotdx-on-data-ui.md §12.2, docs/state-config-v1.md §4).

   POST /api/v1/watch {path} keeps a plugin's query-only `watch` running on the device while a person looks at its
   State; the host stops it about 10 s after the last beat. So it is sent only for an entity whose State a panel on
   screen draws (feed.watchedState(): a samples dataset in use, and the host says the entity supports a watch), only
   while the tab is visible, every 3 s, as today's State tab does (web/detail/d6.js). Nothing else in this page writes:
   no /act, no /config writes, no run input or signal, no /settings, /expect, /forget, /retire or /arrangements (PEP §6, §12.2).
   WHY its own file: the rule is in one place, and a check can read it (tests/test_dx_page.js records every request). */

const EVERY_MS = 3000;

export function startHeartbeat(feed, { api = '/api/v1' } = {}) {
  const last = new Map();   // path -> when its last beat went
  let timer = null;

  async function beat(path) {
    last.set(path, Date.now());
    try {
      const res = await fetch(`${api}/watch`, {
        method: 'POST',
        // the page-only rule (server.py _from_page): the page's header and a JSON body; the browser adds Sec-Fetch-Site
        headers: { 'Content-Type': 'application/json', 'X-IO-Diagnostics': 'ui' },
        body: JSON.stringify({ path }),
      });
      const body = await res.json();
      if (body && body.watch) feed.dispatchEvent(new CustomEvent('watch', { detail: { path, watch: body.watch } }));
    } catch (_) { /* the host is away: the feed says reconnecting */ }
  }

  function tick() {
    timer = setTimeout(tick, 250);
    if (document.hidden) return;
    const now = Date.now();
    for (const path of feed.watchedState()) {
      if (now - (last.get(path) || 0) >= EVERY_MS) beat(path);
    }
  }

  tick();
  return { stop() { clearTimeout(timer); } };
}
