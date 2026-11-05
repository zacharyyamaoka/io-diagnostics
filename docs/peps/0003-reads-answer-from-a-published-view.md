# 0003: A read answers from a published view, and a page never waits for the host

- **Status:** decided 2026-11-04, on Zach's report of the same day. Built as described, measured below. The first landing (9c5147b) had two races, found by the page suites the same day
  and fixed: a run's end, and a page opened during a cold start. Both are under Rules that follow, because both came from this decision and both would come back with a careless rewrite.
- **Applies to:** the classic page and every `GET /api/v1/*`. `web/dx/` reads the same API and gains the same answers; its own loading states are its own.

## Decision

1. **Nothing on a read path builds anything.** A running host has one thread that builds the view and publishes it. Every `GET` reads the latest published view.
   No read builds one, waits for one, or takes the host's lock for one. `Host.view()` stays, for what acts on the world: a write, a run.
2. **What a view does not hold is read off the request.** Who holds a device comes from one pass over `/proc`, in a process of its own, every few seconds while
   someone looks. A page shows the last answer. What acts on it asks for a fresh one.
3. **A page paints from what it has, then fills in.** The `/state` row, or the last answer for that entity. One request at a time per selection, an answer always
   counts, a bound on every load, and past the bound the page says what is slow, how old it is, and offers Retry.
4. **The server is up first.** The socket answers before the log is read and before any driver has described itself. What the last process knew comes back from
   disk (events, history, the drivers' describe answers, the rig's counts), then the drivers run behind it.

In Zach's words: "I don't like how it kinda just like loads... surely maybe there is like a timeout on these things? ... Like for the log shouldn't it just be there?"

## What it answers

The endless "loading…" was a livelock in the page, fed by a slow host and feeding it back. While a page had no answer, every 1 Hz tick and every live event asked `/node`
again, about 20 a second, and an answer counted only if it was the newest one asked for. A `/node` slower than that gap could never land: at 3 s the page sent 645 requests
in 30 s, threw away 595 answers and drew nothing. Preview's took 4 to 22 s, so the page stayed blank. Chrome holds identical GETs back and sends them one at a time (a 4 s
`/node`: 532 sent, 7 answered in 30 s, the newest queued 22 s, two sockets), so the host always had the next one, and that one `/node` walking `/proc` was its 90% CPU.

What did not cause it: the browser's six connections. An unrelated URL answered in 2 ms through all of that, so the Activity log and the release chip were not queued
behind `/node`. "Loading the log…" was the log's own fold (1.5-3.5 s on a host already pegged, with no bound) and "no events yet" a line printed before the first answer. The red
chip is one failed status poll, and a restart gives it one: the port stayed dead while the log was read and the drivers described themselves. That last one is reasoning from
the code and the fixture, not a restart watched on Preview.

| | before | after |
|---|---|---|
| `/node` under CPU load, p50 / p95 (26,867 events, 300 boards a bus) | 852 / 2,021 ms | 4 / 9 ms |
| first useful paint, its `/node` 2.5 s away | never | 13 ms |
| `/node` requests while one 3 s `/node` is answered | 209 in 6 s | 2 |
| a restart: first answer / the log on disk readable | 3.6 s / 3.7 s | 0.2-0.7 s / 0.6-1.7 s, by load |
| `serve` on Preview, a core | 88-93% | 21% with a page, 12-14% with nobody connected |

On Preview with a load of only 14, a `/node` of 0.7 s: the Computer's page sent 280 requests in 20 s, 38 were answered, and nothing was drawn. After: `/node` 2-18 ms, every page
painted in 1-3 ms and answered in 33-234 ms. The rest of the numbers are in
`bench/classic/` (`tools/bench_instant.py`, `tools/bench_classic.js`).

## Alternatives weighed

Criteria, in order: a page cannot get stuck whatever the host does; `/node` is independent of `/proc`, the log and a rebuild; a stale answer is labelled, and a write never
trusts one; the API stays additive; the change stays small.

- **Fix the slow spots and leave the rest.** Needed, and done (`node` serializes the 20 events it sends; `fold` is linear), but each read still builds its own view behind the
  host's lock, the first slow thing to arrive brings the same queue back, and the page stays one slow answer away from the same livelock.
- **A time-to-live on `view()`.** Small, but the first reader after every change pays for the build, under the lock, so `/node`'s p95 is still the build's.
- **A published view, with reads never building (this).** Costs a thread, and an answer is a moment old. That is the right trade: a page that is a quarter of a second behind
  and says so beats one that is exact and blank.

## Rules that follow

- **A new `GET` reads `Host.read_view()`, never `Host.view()`.** `tests/test_instant_pages.py` fails if a read builds on its request's thread.
- **A write publishes before it answers** (`SHOWN_WRITES`, `Host.after_write`), so a person's write is in their next read. A heartbeat or a keystroke does not.
- **A stream waits for the view of its change** (`Host.wait_view`), on its own thread. A request never does.
- **A run's end is one moment for everyone outside.** Its ending waits for a publish, so a client told it is over finds `RunEnded` in its next read. Until then clients are told the run as it
  was, then `state`, `active`, `result` and the stream's `end` all at once, and a second notify says so (`Run._conclude`). The run itself, the host's gates and the disk have the end at once:
  a next run is not refused by this one, and a host that dies in the ending does not read it back as interrupted. The first try held `active` alone. A client could then read `done` with
  `active: true`, and the page keeps a stream's `end` as its last word, so a finished run stayed a running one. Found by the page suites after landing; do not simplify the hold back to one field.
- **A direct call builds.** Only threads that `reads_published` (a request, the feed, the tray, the beat) read the published view; a test or a script asking in-process gets the moment.
- **`View.jobs` is a record of a moment.** It holds copies of the job caches, without their `series`, and is never run or changed. `values_json` and everything at 10 Hz reads the live job, under the lock.
- **What a read pairs with the view comes from the view.** `View.active_runs` is why: the log pairs a run's events with whether it still runs, and read live beside a published view, a run that had just ended read "the host stopped while it ran".
- **A gate that acts is fresh; a gate that shows is not.** `LiveOs.gate` keeps its 2 s promise and may wait for a pass. `LiveOs.peek` never waits. Only a page uses `peek`.
- **`S.node` stays the host's answer for the selection.** The Utilities menu, the 3D drawer and the runs act on it. Only `detailContext` falls back to the kept answer, for drawing.
- **A selection outlives a restart.** An entity the tree has just lost keeps its page, drawn from its last answer, for 20 s (`NODE_GONE_MS`): a restarted host has no row for a board until
  its driver reports. One that stays missing was unplugged, and the page goes to the Computer as it always did.
- **Bounds grow.** A load that fails is retried with a longer bound (6 s to 30 s), so a host that needs 9 s still lands its answer. A fixed bound would be the same livelock.
- **A write is never aborted by a bound.** Aborting a `POST` does not undo it, and it leaves "did it happen?" with no answer.
- **Once there is a feed, its snapshot is a page's base, whichever landed first.** `/state` and the feed are two requests, and the server now answers before the drivers have, so a change can
  fall between them: in neither the `/state` read a moment before it nor any delta, since the feed's baseline already has it. A healthy board's row never changes, so it stayed out of the tree.
  The feed's snapshot and deltas are consistent by construction, and `/state` is only for a page with no feed. Up-first is what made this reachable: anything that assumed "by the time a page
  can connect, the drivers have answered" no longer holds.
