# Runs v1 — a live terminal for every utility (2026-10-24)

Zach, 2026-10-24, while calibrating moteus CAN 1: "it would be cool though if I could perhaps see the terminal output
or something, in case there is debugging information ... or if I needed to enter user input ... the Claude model is
good: you run this and then there is all the terminal's processes you can switch between."

**This file is the seam.** The host side (`io_diagnostics/terminal.py`, `host.py`, `server.py`) and the page's
`IORunViews` registry are built to it; five page proposals (`web/runs/r1.js` .. `r5.js`) draw against it in parallel.
Where code and this file disagree, fix the code or change this file in a commit of its own, never silently.

## 1. A run

Every REAL `act` (never a dry run) is a **run**. A dry run keeps today's path (`runner.run_act`: pipes, no terminal,
nothing recorded as a run). The run's process is the plugin's own `act` command; the host puts it on a pseudo-terminal.

```json
{
  "id": "20261024T153012-a3f9",
  "path": "host/usb1/port-1-3/usb-1-3/can/1",
  "device_id": "usb:1-3", "device_path": "host/usb1/port-1-3/usb-1-3", "plugin": "moteus_servo",
  "action": "calibrate", "title": "Run calibration…", "label": "CAN 1 · Run calibration",
  "danger": "moves", "node": 1, "new_id": null,
  "argv": ["uv", "run", "...", "act", "--device", "usb:1-3", "--action", "calibrate", "--node", "1", "--confirm", "<token>"],
  "started": 1761316212.4, "started_iso": "2026-10-24T08:30:12-07:00", "ended": null, "ended_iso": null,
  "elapsed_s": 12.3,
  "state": "running", "active": true, "stopping": false,
  "exit_code": null,
  "interactive": true,
  "cols": 120, "rows": 30,
  "output_bytes": 4096, "dropped_bytes": 0,
  "stop": null
}
```

- `id` sorts by start time and is unique per host. It is also the `.cast` file name.
- `label` is the tab title: the entity's name, ` · `, the action's title without its trailing `…`.
- `argv` has the value after `--confirm` replaced by `<token>`; the real token is never stored or shown again.
- `started` / `ended` are the **host clock** (`clock.now`, a replay's clock in fixture mode); `elapsed_s` is measured on
  the monotonic clock and keeps counting while `active`.
- `state` is one of:

  | `state` | Meaning | `active` |
  |---|---|---|
  | `queued` | waiting for the plugin's `exclusive` group inside this host (another run, a routine check of this host); no process yet | yes |
  | `waiting` | started, but the rack bus lock is held by another process (a routine diagnose of this or the other channel's host, an experiment): the host asks again until it gets the lock, at most `bus_wait_s` (60 s); §6 | yes |
  | `running` | the process is alive (and, for a moves run being stopped, until the plugin's stop has run: `stopping`) | yes |
  | `done` | exited 0 | no |
  | `failed` | exited non-zero, died on a signal nobody asked for, could not start, or the bus lock stayed held past `bus_wait_s` (`result.outcome` `blocked`, exit code as the plugin's, normally 0) | no |
  | `stopped` | a person pressed Stop (`signal`) and the process then ended non-zero | no |
  | `timed_out` | still alive at the plugin's `act_timeout_s`: the host killed its process group | no |

  A page treats `active` as the one question "is it still going"; an unknown future `state` counts as ended unless
  `active` is true. `done` says the process exited 0, not that the action succeeded: the plugin's records say that
  (`result` below), exactly as for today's result line.
- `exit_code`: the process's exit status; negative = killed by that signal (`-2` SIGINT, `-15` SIGTERM, `-9` SIGKILL);
  `null` while active or when it never started.
- `interactive`: this run was started from the page in THIS host process (a redeemed dry-run token) and is still
  `active`. Only an interactive run takes `input`, `signal` and `resize`. A run started by `io-diagnostics act` at a
  TTY, or one loaded from disk after a restart, is never interactive.
- `stop` is `null`, or, once Stop was requested or a moves run needed the plugin's stop (§6):
  `{"requested": true, "signals": ["SIGINT", "SIGTERM"], "plugin_stop": {"ran": true, "outcome": "ok", "message": "...", "records": [...]}}`.
  `plugin_stop.ran` is `false` with a `reason` when the plugin declares no `stop` verb or the run was not moves-class.

`GET /api/v1/runs/<id>` adds, once the run has ended:

```json
{"result": {"outcome": "ok", "returncode": 0, "duration_s": 31.2, "records": [], "action_result": null, "rejected": [],
            "stderr_tail": "<the last ~800 characters of the terminal, ANSI removed>", "message": "CAN 1 calibrated"},
 "tail": "<the same last lines, for a one-line failure message>"}
```

`result` has the shape `Host.act` returned before runs existed (`records` are the plugin's JSON lines from the record
channel, §3), so the page's result line and Details keep working unchanged. `result.outcome` is `ok | crashed |
timeout | not_started | stopped`.

## 2. API

All JSON, `Cache-Control: no-store`. Reads are GET like everything else; the three writes are gated exactly like
`POST /api/v1/act` (below).

| Endpoint | What |
|---|---|
| `GET /api/v1/runs` | `{"runs": [<run>, ...newest first], "active": N, "limits": {...}}`; every run known: active ones and the finished ones loaded from disk |
| `GET /api/v1/runs/<id>` | one run plus `result` and `tail` when ended; unknown id: **404** `{"error": ...}` |
| `GET /api/v1/runs/<id>/stream[?from=<offset>]` | Server-Sent Events, §4 |
| `GET /api/v1/runs/<id>/cast` | the run's asciinema v2 recording (`application/x-asciicast`), whole file; replayable with any asciinema player |
| `POST /api/v1/runs/<id>/input` | `{"text": "y\n"}`: written to the terminal as typed. At most 4096 bytes. `{"ok": true, "written": 2}` |
| `POST /api/v1/runs/<id>/signal` | `{"signal": "interrupt"}` (the only signal): §6. `{"ok": true, "stage": "SIGINT"}` |
| `POST /api/v1/runs/<id>/resize` | `{"cols": 100, "rows": 24}` (1..500): the terminal's size, so wrapped lines and progress bars fit the view |
| `GET /api/v1/state` | gains `runs`: `{"active": N, "total": N, "items": [<run>, ...up to 30 newest]}` |
| `POST /api/v1/act` | a dry run asked while a run holds the same `exclusive` group is refused at once, `{"dry_run": true, "result": {"refused": "Busy", ...}, "token": null}` (a run can sit at a prompt for minutes; nothing waits for it). A real run (token present) accepts `"detach": true`: answers **202** `{"dry_run": false, "run": <run>}` at once, the process keeps going. Without it the call blocks until the run ends and answers `{"dry_run": false, "result": {..., "run_id": "<id>"}}` as before (existing callers and tests keep working). A refused run (`ConfirmRequired`, `NoPlugin`...) answers `{"dry_run": false, "result": {"refused": ...}}`, no `run` |

`limits`: `{"ring_bytes": 2097152, "input_max": 4096, "cast_budget_bytes": 67108864, "grace_s": 3.0}`.
`result.outcome` is also `blocked` (see `waiting`).

**The three writes** (`input`, `signal`, `resize`) are refused with **403** `{"refused": "<Code>", "error": "<plain
reason>"}` unless the request is the page's own: `NotFromPage` (no `X-IO-Diagnostics: ui`), `NotSameOrigin`
(`Sec-Fetch-Site` not `same-origin`, or `Origin` another site and not in `IO_DIAGNOSTICS_PAGE_ORIGINS` behind the
Stable proxy), `NotJson` (the same `_from_page` check as `/act`, one function). Then, in this order: **404**
`NoSuchRun`, **403** `NotInteractive` (not started from the page in this process), **409** `NotRunning` (already
ended), **400** `BadRequest` (body shape, an unknown signal name, over `input_max`). Nothing reaches the process on
any refusal (a body that is not a JSON object is **400** before any of these). No token: the run's token was spent when it started.

## 3. A real terminal, and where records go

The host starts the plugin's `act` with its stdin, stdout and stderr on the slave side of a PTY (`os.openpty`; the
plugin is a session leader and the PTY is its controlling terminal, so a typed Ctrl-C (`\x03`) is a real SIGINT to its
foreground process group). Default size 120 x 30 (`TERM=xterm-256color`, `COLUMNS` / `LINES` set); `resize` changes it.
Tools therefore print progressively, redraw with `\r`, and can prompt and wait for a line of input.

**Records leave the terminal.** The host also opens a pipe and passes its write end to the plugin as a file
descriptor, named in the environment variable **`IO_DIAGNOSTICS_RECORD_FD`**.

- **Set:** the plugin writes its JSON-line records (the `action-result`, Events like `ActionCompleted`, anything the
  host parses) to that fd, one object per line, and nothing else to it. Everything a person would read goes to
  stdout / stderr (the terminal). The host reads the record fd as the lines arrive and parses them when the run ends.
- **Not set** (dry runs, `diagnose`, `probe`, `follow`, every other verb, and any old plugin that ignores the
  variable): stdout stays the record channel, exactly as before. A plugin that does not know the variable still works
  under a run: the host finds no record on the fd, so it falls back to the lines of the terminal that parse as a JSON
  object with a `"record"` key (they also show in the terminal; that is the price of an old plugin). A line that is
  not a record is not "rejected" in that mode: a terminal legitimately holds other text.
- The fd is inherited only by the plugin (`pass_fds`); the plugin's own children get it closed by Python's default
  `close_fds`. A plugin that wraps a vendor tool lets the tool **inherit the terminal** (stdin / stdout / stderr as it
  received them) so its output is live and it can prompt; only the plugin's records use the fd.
- The plugin decides it is under a terminal with `os.environ.get("IO_DIAGNOSTICS_RECORD_FD")` and `isatty(0)`.

Also set for an act: `IO_DIAGNOSTICS_DEVICE_PATH`, `IO_DIAGNOSTICS_DEVICE`, `IO_DIAGNOSTICS_NOW`,
`IO_DIAGNOSTICS_SCHEMA` (unchanged) and **`IO_DIAGNOSTICS_RUN_ID`**.

## 4. Output: ring, recording, stream

- **Ring buffer** per run, `ring_bytes` (2 MiB) of raw terminal bytes in memory. A burst past it drops the oldest bytes;
  `dropped_bytes` counts them. Offsets are absolute byte counts since the start (never reset by a drop).
- **Recording**: every run is written, as it happens, to `<state dir>/runs/<id>.cast` as **asciinema v2**
  (`{"version": 2, "width": 120, "height": 30, "timestamp": <unix>, "env": {"TERM": "xterm-256color"}, "title": "<label>"}`
  then `[seconds_since_start, "o", "<text>"]` per chunk; UTF-8 decoded incrementally, so a multi-byte character split
  across two reads is one character) and `<id>.json` (the run and its `result`, rewritten when it ends). State dir =
  `$IO_DIAGNOSTICS_STATE_DIR`, else `~/.local/state/io-diagnostics` (`host.default_state_dir`). The host also writes its
  own dim notes into the stream (and so the recording): `$ <argv>` first, the plugin-stop line (§6), `[exit N · 12.3 s]`
  last. A host built without a state dir (tests, a fixture replay with no `IO_DIAGNOSTICS_STATE_DIR`) keeps runs in memory only.
- **Retention**: finished runs persist. Total size of the `.cast` + `.json` files is capped at `cast_budget_bytes`
  (64 MiB); past it the oldest ENDED runs are deleted until under (an active run is never pruned). On start the host
  loads the finished runs from disk; a `.json` that says `running` or `queued` (the host died) is loaded as `failed`
  with `result.message` "the host stopped while it ran". A loaded run's stream and `cast` replay from its file.
- **`GET /api/v1/runs/<id>/stream`** is SSE (`text/event-stream`, no CORS header: same-origin only, like `/events`):

  ```
  event: hello    data: {"run": <run>, "from": 0, "available_from": 0, "dropped_bytes": 0}
  event: output   id: <offset after this chunk>   data: {"offset": 0, "t": 0.012, "b64": "<base64 of the raw bytes>"}
  event: end      data: {"run": <run + result>}
  : keepalive     (a comment every 15 s while nothing happens)
  ```

  It replays the ring from `from` (default: the oldest byte held; `available_from` says where that is, > 0 when the
  ring dropped data), then streams live output, then sends `end` with the final run and closes. A run that already
  ended replays and ends at once. Replayed bytes are merged into events of up to 64 KiB; live ones are sent as they are
  read, so output reaches the page as the tool printed it (measured in tests/test_runs.py). `t` is seconds since the
  run started. **Bytes, not text**: base64, because a chunk may end inside a UTF-8 sequence; write them to xterm.js as a
  `Uint8Array`. A client that lost the connection reconnects with `from=<last id>`. The page reads the
  stream with `fetch`, not `EventSource` (which would reconnect after `end` by itself), so `end` is final.

## 5. Activity

One event per run, so actions are finally in the log. Both are Events of type `run` (`GET /api/v1/activity?types=run`),
`source` `io_diagnostics.runs`, `path` the entity the action ran on, `args.run_id` the link:

| `code` | `severity` | `message` (example) | `args` |
|---|---|---|---|
| `RunStarted` | info | `Run calibration started on CAN 1` | `id` `run:<run id>:started`, `run_id`, `action`, `node`, `plugin`, `danger` |
| `RunEnded` | info (done) · warn (stopped, timed_out) · error (failed) | `Run calibration on CAN 1 stopped after 12 s (exit -2); the plugin's stop sent` | `id` `run:<run id>:ended`, `run_id`, `state`, `exit_code`, `duration_s`, `stop` |

`stamp` is the host clock. Events live in memory for this host process; the persisted runs are the long record. A
queued run says `RunStarted` when its process starts, not when it is queued.

## 6. Input, Stop, and the plugin's stop

- **Input** is bytes into the PTY master: `text` is UTF-8 encoded and written as typed (the terminal echoes it; the
  line discipline handles Enter, Backspace and Ctrl-C). A prompt like `Continue? [y/N]` is answered by `{"text": "y\n"}`.
- **Stop** is `signal: interrupt`: SIGINT to the plugin's process group at once; if the group is alive after
  `grace_s` (3 s) SIGTERM; after another `grace_s` SIGKILL. The process group is the plugin's session, so wrapped tools
  that stayed in it die with it. Repeating the call is a no-op (`stage` says where it is). On a `queued` run it cancels
  the wait (`stopped`, nothing ever ran). The final state is `stopped` when the process ended non-zero after a Stop,
  `done` if it still exited 0.
- **A person's press waits its turn for the rack bus lock (2026-10-24; Zach pressed Calibrate and got `ActionBlocked: rack
  bus lock held by 356724 moteus_servo.diagnostics`, a routine diagnose having the lock at that moment).** A plugin refuses an
  act whose bus lock is held with the event `ActionBlocked` whose `args.reason` is `BusLockHeld` and whose message says
  `held by <pid> <program>`. The host does not pass that on to the person: the run goes `waiting`, its terminal prints
  `waiting for the bus: held by <program> (pid N) · checking <device> ...` (again when the holder changes, and every
  10 s with how long it has waited), and the host starts the act again every `bus_poll_s` (0.5 s) until it gets through the
  plugin's normal gates (port-holder check, bus lock, token), then `the bus is free: running now` and state `running` (an
  attempt still alive `bus_settle_s`, 4 s, after a wait got through the gate). Past `bus_wait_s` (60 s) the last refusal
  stands: state `failed`, `result.outcome` `blocked`, the plugin's message with the holder named, `(waited 60 s for it)`.
  Stop while waiting cancels it (`stopped`). A start that never got the bus never touches the board, so the plugin's stop
  (below) is NOT sent for it. Only `BusLockHeld` is waited for: `PortBusy` (a person has tview open) is refused at once as
  today. WHY the host asks again, not a blocking flock in the plugin: it works for every plugin that refuses this way
  (moteus_servo today), a plugin keeps LOCK_NB for `diagnose` and `probe` so routine checks yield to people and experiments
  and never the reverse, and the retry is only ever of a start that did nothing.
- **A person's run is not starved by routine checks.** `Gates.hold(group, person=True)` is what a real act and a dry run
  take: while one is waiting or holding the plugin's `exclusive` group, no scheduled diagnose or probe of the group starts
  (`try_take` is False, a blocking routine hold waits), and one that queued behind it goes after it. A check already running
  finishes first (bounded by its own timeout); a check of the other channel's host is not under this host's gates and is
  met by the retry above.
- **The plugin's own stop (moves-class runs only).** A plugin that can leave a device energised declares in `describe`
  `"stop": true` and implements the verb `<command> stop --device <id> [--node N]`: it puts the device in its quiet
  state (moteus: mode 0, the real path of `stop_all`), needs **no `--confirm`** (stopping is the safe direction),
  prints JSON lines like `act`, and must be safe to run at any time. When a run whose `danger` is `moves` ends in any
  state other than `done` (stopped, timed_out, failed, including a typed Ctrl-C), the host runs that verb itself, after
  the process has exited, inside the same `exclusive` hold, bounded to 30 s, on a pipe (no terminal), for the run's
  `--device` and `--node`. It records what happened in `run.stop.plugin_stop`, writes a note into the terminal
  (`stopping CAN 1 (the plugin's stop) ... mode 0`), and the run stays `active` / `stopping` until it has finished. A
  moves run on a plugin without `stop` says so in the terminal and in `RunEnded` (warn): the hardware may still be driven.
  The plugin's stop is host-initiated only; the page has no endpoint for it.
- The `moteus_servo` stop is scoped to what the run drove: `--node N` stops CAN N, no node (an adapter-level run such as
  Identify (each board)) stops every board that answers (`stop_all`'s scan and tool).

## 7. The page seam

`io_diagnostics/web/runs/` mirrors `detail/` and `utilities/`: `index.json` (pre-listed r1..r5, `ready` flag; a lane
flips only its own line), `core.js` (the runtime, `window.IORuns`), and one file per proposal registering
`window.IORunViews[id]`. The status bar's **Prototype** group gets a third select, `#run-view` ("Runs: r1 · Terminal
panel ... r5"), remembered in `localStorage` key `io-diagnostics.runs-view` like the other two; default `r1`.
`proto-switch` shows when the three registries list more than three entries together.

```js
window.IORunViews.rN = {
  name: 'Terminal panel with tabs', description: '...', order: 1,
  mount(ctx)           // once, when this view becomes the selected one
  unmount(ctx)         // when another is selected: remove everything mount added (DOM, listeners, streams)
  update(ctx)          // after every page render and whenever ctx.onChange fires; must be cheap and idempotent
  open(id, ctx)        // show this run (the Utilities run line's "Show output"; ctx.open calls it)
  runLine(util, run, ctx) -> html | null   // replace the Utilities run line for this run; null = the default line
};
```

`ctx` is one object, the same for every call, its functions live (never stale):

| Member | What |
|---|---|
| `ctx.runs()` | the run summaries, newest first (the page's copy of `/api/v1/state` → `runs.items`, plus any run fetched by id) |
| `ctx.run(id)` | one summary or `null` |
| `ctx.onChange(fn)` | `fn()` when the list or any run's state changes; returns `off()` |
| `ctx.stream(id, onData, onEnd)` | replays then follows `/runs/<id>/stream`. `onData(bytes: Uint8Array, {offset, t})`, `onEnd(run)` once; returns `stop()` which closes it. Reconnects from the last offset on a dropped connection |
| `ctx.sendInput(id, text)` / `ctx.interrupt(id)` / `ctx.resize(id, cols, rows)` | the three writes, with the page's headers. `Promise<{ok, status, data}>`; never rejects |
| `ctx.terminal(el, id, opts)` | the shared xterm.js binding: creates a `Terminal` in `el`, fits it to `el` (a `ResizeObserver`; the new size goes to `resize`), replays and follows the run's output, forwards typing to `sendInput` while the run is `interactive` and `active` (read-only otherwise: a finished tab is a scrollback), and shows a dim "run ended" line. Returns `{term, fit(), focus(), dispose()}`. `opts`: `{fontSize, scrollback, readOnly}` |
| `ctx.castUrl(id)` | the recording's URL |
| `ctx.open(id)` | ask the selected view to show a run (calls its `open`) |
| `ctx.redraw()` | re-render the shell / detail now (the existing `redraw` seam) |
| `ctx.root` | an empty, full-width container between the panes and the status bar (`#runs-root`) that the view may use; hidden until it sets `hidden = false` |
| `ctx.statusSlot` | a container in the status bar, left of the Prototype group, for a toggle or a badge |
| `ctx.css(key, text)` | installs a `<style data-runs-css=key>` once (each view ships its CSS inside its own file) |
| `ctx.pref(key, fallback)` / `ctx.setPref(key, value)` | `localStorage` under `io-diagnostics.runs.<key>`, try/catch-wrapped |
| `ctx.h` | `{esc, shape(level, title), plainTitle(title), elapsed(run) -> "1:23", stateWords(run) -> "running" / "done" / "stopped" ...}` |

The shell also owns: the **default run line** (a utility that has a run draws `running… 12 s · Show output`; the
button is `data-run-open="<id>"` and calls `ctx.open`; with a finished run, the result line plus `Show output`),
the detach flow (`POST /api/v1/act` with `detach: true`, the run id kept on the page's `util`, completion read from
`/state` → `runs`), and the `Runs` select. A view never calls `/api/v1/act`.

**xterm.js** is the terminal emulator (the one VS Code and Jupyter use), vendored for the offline page in
`web/vendor/xterm/` (`xterm.js` + `xterm.css` 6.0.0, `addon-fit.js` 0.11.0, MIT, `NOTICE.md`), loaded by `index.html`.
Panes are resizable: every boundary a view adds drags, remembers its size (`ctx.setPref`), restores on double-click and
moves by arrow keys when focused (Zach's standing rule).

## 8. Tests the lanes can rely on

- `tests/fixtures/plugins-terminal/` is a fixture plugin directory: `terminal_plugin.py` (describe / diagnose / act /
  stop, never touches hardware; it logs every verb to `$TERMINAL_LOG`) + `terminal.toml`. Its `act`:
  `--action progress` prints lines with `\r` progress, prompts `Continue? [y/N]`, reads stdin, writes its records to
  the record fd, exits 0 (or 1 on `n`); `--action calibrate` (danger `moves`, node 1) does the same slowly;
  `--action hang` ignores SIGINT; `--action old` prints its records on stdout (an old plugin). `stop` records itself in
  the log. `serve --fixture F2 --plugins tests/fixtures/plugins-terminal --port 0 --no-udev` runs it on a fixture host.
- `tests/test_runs.py` (host, server, gates, recording), `tests/test_runs_view.js` (the page: the registry, the Runs
  select, r1) — run exactly as the gates in the build plan.
- Hard rule for every lane: a test never runs a real `act`, `serve` or `once` on a live host; fixtures only.
