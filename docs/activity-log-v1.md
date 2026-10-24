# Activity log v1: one stream, two channels (2026-10-24)

Zach, 2026-10-24, on the research (`reports/media/activity-log-research-2026-10-23.html`, vault note "Activity Log — One
Stream, Two Channels (2026-10-23)"): "I feel confident you can come up with a good design. I will take your defaults...
Please go ahead with implementing this vision for your logs." This file is what was built, and where it went beyond the six
defaults. Where code and this file disagree, fix one in a commit of its own.

## The six decisions, at their defaults

1. **Two channels, Activity and Raw.** Coded events are Activity; uncoded lines are Raw. Raw is off except in an entity's own
   log (a scope turns it on; the Raw button overrides either way).
2. **Status transitions** (`LevelChanged`). A warn or error transition rides on its fault row. Silence (to `stale` or
   `unknown`) is its own warn row only when no plug or unplug on its path explains it. Every other transition is `debug`.
3. **Faults fold onto their cause.** A `FaultConfirmed` is a "→ fault confirmed" badge on the producer's own row. Faults the
   host confirmed in its first reports are one "Found at start" row.
4. **The host's events persist.** Append-only JSONL, one file per boot, `~/.local/state/io-diagnostics/log/`, about 20 MB.
5. **Adapters: only the pure `to_ros_log` / `from_ros_log` pair** (`docs/diagnostics-record-v0.md` "v0.2"). journald and MCAP later.
6. **Order: newest first.**

## The shape: a store, a fold, a view

```
producers ─► Host.view() ─► EventLog (JSONL, 1 file per boot) ─► logview.fold ─► GET /api/v1/activity?fold=1 ─► web/activity/log.js
 kernel log    derived      history + current, by id            pure function     rows, scope, floor, channel      the table
 faults        events       received = first seen               of the events                                       (+ runs/r4.js)
 runs, plugins                                                  (restart-proof)
```

- **`io_diagnostics/eventlog.py`**, the store. `<state dir>/log/<boot start UTC>-<8 hex of the boot id>-<segment>.jsonl`, one
  `{"key": "<event id>", "event": <to_dict>}` per line. A boot is the machine's (`/proc/sys/kernel/random/boot_id`), so a host
  restart appends to the same file and a reboot starts another. The log is read at start (newest 50,000 events kept in memory).
  The state dir is `$IO_DIAGNOSTICS_STATE_DIR` or `~/.local/state/io-diagnostics`; a fixture replay writes only when it is named,
  and `once` never writes.
  - **Facts, not views.** A line is an event as the host saw it. One whose content changes later (a `LinkSpeed` that sysfs turns
    into a `LinkDowngraded`) is written again with the same key; the newest line wins. The sequence number is not compared
    (it is the order a process happened to make events in).
  - **`received`** is when the host FIRST saw the event (OpenTelemetry's ObservedTimestamp). It is written once and survives
    restarts; `stamp` stays the source's clock.
  - **Only settled events are written.** A kernel-derived USB event can still turn into another (a "Plugged" into a
    "ReEnumerated" 2 s later), so it is written once it is 30 s old; every other event is final when the host has it.
    Provisional events (udev's "plugging in…") are never written.
  - **Cap.** A segment closes at 5 MB; the OLDEST segments are deleted while the total is over 20 MB, never the one being
    written. Their events leave memory with them. Settable for a test (`event_log_cap_bytes`, `event_log_segment_bytes`).
  - **Several hosts, one boot** (Stable and Preview run side by side): each write is one `O_APPEND` line, and before writing a
    host reads what the others appended, so a fact is written once. A duplicate that slips through is harmless (newest wins).
  - **A full or read-only disk never stops the host**: the log stays in memory and says so (`log.note` on the API).
  - The host also writes when nobody is looking (every refresh) and once more when it stops.
- **Event ids.** A plugin's or the fault manager's sequence restarts with every process and depends on which plugin answered
  first, so ids that name the fact are used: `fault:<code>:<path>|<fault>|<stamp>` for the fault lifecycle, and `source#seq@stamp`
  (it was `source#seq`) for a plugin's own events. A host's own events keep their `args["id"]`.
- **`io_diagnostics/logview.py`**, the fold: a pure function of the events (a restart, a second host and a test see the same
  rows). The rules, in the order a row is decided, are in the module's docstring and below.
- **`GET /api/v1/activity?fold=1`** and `GET /api/v1/events?fold=1` (docs/api-v1.md "The Activity log model"). Without `fold`
  the endpoints answer as before (the raw events), so every existing client keeps working.

### What the fold does

| Rule | What | WHY |
|---|---|---|
| Fault lifecycle | `FaultConfirmed` rides on the producer's own event it repeats: same entity, no more than 10 s before it, warn or worse (the closest; an info event is never a cause). `healed` / `cleared` update the badge and stay rows of their own | "One fact, one row": 13:34:07 and 18:11:44 each showed twice. A heal is a later fact (the host noticing it recovered), so it keeps a row, pointing back (`args.confirmed_row`) |
| Found at start | A confirmation with no cause that the host made in its first 30 s (`Faults` marks it `args.at_start`) joins one `FoundAtStart` row per host process (`path` `host`, `args.faults`) | A WARN confirms on its 3rd report, so four of them land in the same second at start: the memory catching up, not four things that happened then |
| Transitions | to warn / error: rides on its fault row (a member of it) or, with none, a `debug` row. To stale / unknown: a `warn` row only when no plug, unplug, re-enumeration or flap on its entity's path (or ancestors) in the 2 min before explains it. Every other: `debug` | A transition to warn is already the fault; silence is not a fault, but a person wants to see it when nothing explains it |
| One run, one row | `RunStarted` + `RunEnded` become `Run` (stamp the start, `last_stamp` the end, `args.state`) | The run's output is the Raw channel under that row |
| Coalescing | The same `code`, `source`, `path`, `message` and `severity`, each within 60 s of the last: one row, `count`, `last_stamp`. Plugs, boots and the fault lifecycle never merge | `ProbeBlocked` appeared 5 times in a minute while the bus was busy. Rows sort by `last_stamp` (K8s `lastTimestamp`) |
| Select | `path` + `scope` (`children` = the entity and everything below it, plus rows naming its hardware; `self`; `host` = children plus every ancestor), `severity` floor, `types` | Rerun's `$origin/**`, journalctl's device match |

### The Raw channel

Uncoded lines: `GET /api/v1/activity?fold=1&channel=raw&path=<entity>`: this boot's kernel lines (the host already reads them for the
plug events: `journalctl -k`, only the USB / ACM / UVC / AER lines it asked for), each placed on its device (a USB device's lines
are its port's) or the port; severity from `io_facts.kernel_log`'s own patterns (`device descriptor read/64, error -71` is
warn). Raw rows are never stored: the journal keeps them. The other Raw source is a run's output, which is the page's
(`runs/r4.js` below). Not done, deliberately: the whole journal (about 1,275 lines an hour here), `/rosout` by logger name,
the exact `journalctl` command for any entity (a kernel row's detail shows one for its port and time).

## The page: `web/activity/{core.js,log.js,log.css}`

One calm table, newest first. Columns are rqt_console's: No · Time · Severity · Entity · Code · Message · Source. The first screen
is one answer ("nothing needs you in the last hour", or "2 warnings in the last hour"). Colour appears only on a row the host
logged at warn or worse (it carries `data-diagnostic`, the rule `tests/test_tree_variants.js` measures).

- **Severity floor** (Debug+ Info+ Warn+ Error+, default Info+, remembered). The typed search cannot lower it (Foxglove's rule); the
  footer says so when it tried.
- **Typed search** (Logcat, Console.app): `level:warn` is that severity and above; `code:` `entity:` `path:` `source:` `type:` `kind:`
  `port:` `hw:` `message:` `run:` `channel:` `fault:` search a field; `-code:Plugged` negates; `"two words"`; `/regex/`; bare
  words must all match. The same key is ORed, different keys are ANDed.
- **Hide / Highlight** (rqt_console's exclude and highlight lists): the same words, kept as chips, remembered; "highlighted only";
  a row's detail offers "Hide <code>" and "Highlight <code>".
- **Views**: All · Faults · Runs. **Raw**: on in an entity's log, off elsewhere, overridable.
- **Scope**: clicking an entity name opens its log: + children · this only · + path to host, with Raw on. `IOActivityLog.create({scope,
  compact: true})` embeds the same log in a panel: the entity-page lane's seam.
- **Live tail with pause**: new rows arrive over `GET /api/v1/events?fold=1` (a stream of changed rows; the page re-reads the window
  on one, and every 8 s). Scrolling away or opening a row pauses it: the order is frozen (a row still updates in place), new
  rows are counted ("Paused · Resume (3 new)"), and Resume brings them in. A changed filter re-freezes on its answer.
- **Volume strip**: events over time by severity (a repeat counts as every event it stands for); drag across it to select a time
  range, click to clear.
- **Detail** of a row: what it folds, its details, the 30 s before it (a fault's "breadcrumbs", Sentry), the `journalctl` command
  for a kernel row, and Show only this entity · Open its page · Hide / Highlight · Copy as JSON.
- **Every boundary drags, is remembered (localStorage `io-diagnostics.activity.*`), resets on double-click and moves with the
  arrow keys**: the detail pane, the volume strip, each resizable column (Entity, Code, Source). `/` focuses the search, the
  arrow keys walk the rows, Esc closes the detail.
- `core.js` has no DOM (the search, the filter pipeline, the strip's bins, the scope rule, the terminal-output line assembler)
  so `tests/test_activity_view.js` runs it in node and the mutation checks can break it.
- `app.js` changed only in the Activity view: `renderActivity` is now `renderActivityView`, which shows `#activity-host` and
  hands the log its own DOM (the page redraws once a second and would wipe a search box); `S.actFilter`, `S.ran` and
  the "utility" lines the page used to add are gone (the host logs the run).

## r4: Runs in the Activity log (`web/runs/r4.js`)

Against the frozen seam of `docs/runs-v1.md` §7, no member added. A run is one row (`Run calibration on CAN 1 · running…`) and its
output streams under it as lines: the terminal's bytes through `activity/core.js createLineAssembler` (escape sequences dropped,
a `\r` progress bar is one line), a reply box that answers the prompt (`ctx.sendInput`), Stop on the row (`Stop (motor goes limp)`
for a `moves` run, `ctx.interrupt`). The view owns the output (`ctx.stream`, one stream per run it was asked to show) and hands the
lines to the log (`IOActivityLog.attachRuns`); the rows, the folding and the filters are the log's. `ctx.root` holds an embedded
log scoped to the run's entity (children, Raw on), opened by `open(id)` and by a new run, with a status-bar toggle ("Run log",
badged with the runs going), a draggable remembered height and "Open in Activity". When the Activity view is what is on screen,
`open(id)` opens the run's output in it. `runLine` returns `null` (the default line, "Show output"). WHY lines and not xterm.js: a tool
that prints and asks a question is this view's job; a full-screen program (vi, less) is r1's.

## Beyond the six defaults (each reversible)

- **`RECORD_VERSION` is `"0.2"`** (it was `"0.1"`): the Event gained fields. `tests/test_record.py` asserted `"0.1"`.
- **Healed and cleared stay rows** of their own beside the "→ fault confirmed" badge, which says when it healed. The spec said the
  lifecycle shows as a badge; a heal is a later fact and a person watching the top of a live tail would otherwise not see it.
- **The default `GET /api/v1/activity` is unchanged**; the log model is `fold=1`. Every existing client and test reads the raw events.
- **Found-at-start window is 30 s** from the first report the process saw, marked when the event is made (`args.at_start`).
- **The cause window is 10 s; coalescing gap 60 s; "explained" window 2 min; settle 30 s.** Constants in `logview.py` / `eventlog.py`.
- **The log is also kept when nobody is looking** (a write on every refresh and at stop), not only when a view is built.
- **Row numbers ("No")** are the row's place in the loaded window, oldest = 1; filters leave gaps. The host has no global sequence
  (`seq` is per source), so a gap the filters do not explain cannot be detected.
- **Raw lines are kernel lines only** in the host; run output is the page's (r4). Raw rows duplicate the coded kernel events by
  design (the coded row says what happened, the raw line is the kernel's words).
- **Fixture replays**: an event of the recording that falls after the replay clock is held back (as before), and the log may keep
  one written before its clock reached it (it is shown again only when the clock passes it).

## Proof

- `tests/test_activity_log.py` (the record, the fold, the store: coalescing, fault folding, found at start, the transition rule,
  runs, scope and floor, the Raw parser, persistence across a restart with the cap and pruning, two hosts on one boot),
  `tests/test_activity_log_host.py` (the real host and server on the rack's frames; a restarted host; a reboot; the API).
- `tests/test_activity_view.js`: the page's logic in node, and the real page in headless Chrome on fixture hosts (`F1`, `F1` started
  before its story, `F2` with the terminal fixture plugin for r4).
- Mutation checks, `tests/mutate_activity_log.py`: 38 key rules (the gap, the cause window, the settle window, the cap, `received`,
  the floor, the search's AND / OR, the stock levels and stamp, ...) are each broken in a copy of the tree and a test must fail.
- `docs/record_activity_hero.js` records the hero video and GIF of the primary flow from the real page.
