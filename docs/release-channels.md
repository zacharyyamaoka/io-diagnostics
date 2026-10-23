# RobotDiagnostics' Stable / Preview channels

*2026-10-23 (Zach: "Please add the stable preview switch"). The channels come from the shared runtime,
`/home/bam/stable_preview_switcher` (its `docs/CONTRACT.md` is the interface; its `skill/SKILL.md` is how to use it).
This file holds the WHY for `stable-preview.json`, which JSON cannot carry.*

## What there is

| | Where | What it runs |
|---|---|---|
| **Stable** | http://127.0.0.1:8820/ | the runtime's Stable server: a pinned copy of `io_diagnostics/web/` from the last **Make Stable**, `/api/` forwarded to the release's own `cli.py tray --port 8822` — the dashboard backend AND the top-bar chip, one process |
| **Preview** | http://127.0.0.1:8821/ | `cli.py serve` straight from the main checkout (`main` as it is now), restarted by the runtime whenever its Python or a plugin declaration changes |
| **Agent lanes** | `sps dev` in a worktree | the same `serve` from that worktree, on a lane port (5400–5499) |

- The floating chip (top-left, over the page's own top-bar mock) on every channel: Stable shows Open Preview and Roll
  Stable Back; Preview shows Make Stable.
- The dock icon (`~/.local/share/applications/robotdiagnostics.desktop`, written by `sps install-desktop`): click =
  Stable; right-click = Open Preview (live — what agents are changing), Make Preview Stable, Roll Stable Back.
- The tray belongs to Stable: it is Stable's `app` process, so Make Stable and Roll Stable Back swap it with the page.
  Its Open dashboard runs `gtk-launch robotdiagnostics` (tray.py `as_installed`), i.e. the dock icon's click.
- At login `~/.config/systemd/user/robotdiagnostics.service` runs `sps open --no-open` once (below).

Operate it from anywhere (the dock and the chip do the same):

```bash
sps --root /home/bam/io-diagnostics status
```

```bash
sps --root /home/bam/io-diagnostics promote
```

`sps --root /home/bam/io-diagnostics rollback` (press again to roll forward) and `sps --root /home/bam/io-diagnostics
stop --all` change what Zach uses: Zach's calls, never an agent's.

## `stable-preview.json`, key by key

- `app: "robotdiagnostics"`: the id the old desktop entry, icon and WM class already used, so the dock icon and the
  icon theme entry stay the same.
- `icon: brand/placeholder/robotdiagnostics.svg`: the placeholder face, byte-identical to the icon already installed
  as `robotdiagnostics` (until a logo is picked from `brand/logos`).
- `stable.port: 8820`: Zach's muscle memory (the old service's port). The page's origin is unchanged, so its
  browser-stored settings key off the same origin.
- `stable.build` copies `io_diagnostics/web` to `.sps-site` (`output`): the page is static files; the Stable server
  serves that copy and the release's `source/` keeps its own `web/` for the backend. `/bin/cp` by absolute path, so the
  build does not depend on PATH.
- `stable.spa: false`: the app's own server answers a missing path with 404, not with the page; Stable does the same.
- `stable.processes[app]`: `/usr/bin/python3 {source}/io_diagnostics/cli.py tray --port 8822`. The system python,
  because the tray needs its `gi` (GTK + AyatanaAppIndicator3); from `{source}`, so the dashboard backend, the plugin
  declarations (`plugins.d/`) and the tray all run the pinned release. ONE process for tray and API, because two live
  hosts would run every driver plugin twice against the same rack.
- `stable.proxy: {"/api/": "app"}`: the page calls `/api/v1/*` same-origin, as it does on the app's own server.
- `IO_DIAGNOSTICS_PAGE_ORIGINS` (on the `app` process): the Stable server forwards `/api/` with `Host` rewritten to
  127.0.0.1:8822, so a page write (`POST /api/v1/act | settings | expect …`) carries `Origin: http://127.0.0.1:8820`,
  which no longer equals `http://<Host>`. `server.py` `_from_page` also accepts the origins named here; nothing else
  sets it, and `Sec-Fetch-Site: same-origin` plus the page header are still required.
- `PATH` in `stable.env` and `preview.env`: the driver plugins run `uv run …` (`uv` is in `~/.local/bin`), and a
  process started by the dock or by systemd does not get the shell's PATH (the old service set the same value). It is
  set on both channels because the runtime removes every key a config names from what it passes down (CONTRACT
  §9.20): set only on Stable, Preview would start with no PATH at all.
- `PYTHONUNBUFFERED=1`: the process logs (`~/.local/state/stable-preview-switcher/robotdiagnostics/run/*/*.log`) get
  each line as it is printed.
- `preview.command`: `/usr/bin/python3 {checkout}/io_diagnostics/cli.py serve --port {port}`, the app's own server,
  which does the three duties of a non-Vite Preview (CONTRACT §10.7) in `io_diagnostics/release_channels.py`: `/__sps/*`
  to `sps api`, the runtime's `chip.js`, the chip tag in each page. Only when the runtime runs it as Preview or a lane
  (`SPS_CHANNEL` preview / dev); run by hand it is exactly the old server. The system python, like the tray, so both
  channels judge with the same interpreter.
- `preview.health` (and the process's): `/api/v1/health`, the host's cheap liveness answer.
- `preview.watch`: `io_diagnostics/**/*.py`, `io_facts/**/*.py`, `contract/**/*.py`, `plugins.d/*.toml`. The runtime
  restarts Preview when an agent's merge changes the host or a plugin declaration (CONTRACT §10.11; added to the
  runtime for this app). The page (`web/`) is read from disk on every request and needs no restart; `plugins/` code
  runs as a fresh subprocess each diagnose. Anchored patterns, so the watcher never walks `reports/` or `.venv`.
- `snapshot.exclude`: `docs/`, `reports/`, `tests/`, `prototypes/`, `brand/`, `out/`: nothing the running app reads,
  so a docs or report commit does not make the chip say "Stable is behind", and `reports/` (≈190 MB of media) never
  goes into a release.
- `chip.position: top-left`, measured (elementFromPoint over the chip's rectangle at 1400×900 and 1280×720): the
  default bottom-right covered the Prototype selects (`#detail-layout`, `#utility-menu`), top-right the page's own tray
  chip button; top-left and bottom-left covered no control, and top-left only hides the top-bar mock's "Activities"
  label where bottom-left would hide "Last update N s ago".

## The login service

The tray used to be the whole service (`cli.py tray --port 8820` from a pinned detached worktree). Now the runtime owns
Stable, tray included, and the service only brings Stable up at login:

```ini
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStartPre=/bin/sh -c 'while ss -ltnH "sport = :8820" | grep -q . && ! curl -sf -o /dev/null http://127.0.0.1:8820/__sps/health; do sleep 15; done'
ExecStart=/usr/bin/python3 /home/bam/.local/share/stable-preview-switcher/runtime/sps.py --root /home/bam/io-diagnostics open --no-open
ExecStop=/usr/bin/python3 /home/bam/.local/share/stable-preview-switcher/runtime/sps.py --root /home/bam/io-diagnostics stop
TimeoutStartSec=infinity
Restart=on-failure
RestartSec=15
```

- WHY the runtime owns the tray rather than systemd: the tray is also Stable's API backend, and only the runtime knows
  which release is Stable. A unit pointed at "the current release" would need its own copy of that pointer, would not
  follow Make Stable or Roll Stable Back, and the runtime refuses to proxy to a port it did not start.
- WHY the wait before `sps open`: port 8820 may be held by a dashboard started by hand. `sps open` would start the tray
  (8822) and only then find 8820 taken and fail, and a restart loop would flash a tray icon every 15 s. The loop waits
  until 8820 is free or already answers as this app's Stable, so Stable takes over once the hand-started one stops.
- WHY no `SPS_NO_WINDOW`: everything the service starts inherits its environment, including the tray, whose Open
  dashboard runs `sps open` through gtk-launch; with it set, that click would open no window.
- `RemainAfterExit`: the started processes live on in the unit's cgroup; stopping the unit (logout) runs `sps stop`.
  A tray that crashes stays down until the next dock click or `sps open` (the old unit restarted it).

## Proof (2026-10-23)

In a scratch clone (passive plugins only, `SPS_STATE_HOME` / `SPS_PORTS` scratch, `SPS_NO_WINDOW=1`, the tray on a
private Xvfb display and session bus): headless Chrome read the chip in its shadow root on Stable (match, grey Open
Preview), committed on main (behind, blue Open Preview), pressed Open Preview (this window moved to Preview: Make
Stable), pressed Make Stable (match), pressed Roll Stable Back ("Stable was rolled back", Reload, then behind); the
app's API answered through Stable's `/api/` proxy (26 devices) and a page write passed the page gate there; a watched
`.py` edit restarted Preview; `sps dev` served a lane with the chip. Server side: `tests/test_release_channels.py`.
