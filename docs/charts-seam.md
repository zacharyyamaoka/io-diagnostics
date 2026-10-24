# Chart views: how a sparkline enlarges (lane chart-enlarge, 2026-10-24)

*Zach, 2026-10-24: "Make 5 proposals for how you can kinda like enlarge the spark line... in case you want to kinda see it more clearly
the line for some reason." Five proposals, one shared helper, one bottom-right Prototype select ("Charts": c0 .. c5), never a URL flag.*

## The one call a page makes

```js
IOCharts.wrap({ p: <entity path>, l: <label>, s: [[<status name>, <measurement key>, <style?>], ...] }, sparklineHtml)
```

It returns the sparkline's grid cell: `<span class="ch-spark" data-spark=...>`. Put it where the sparkline's own cell was (it is a
block, a direct child of the row's grid), keep drawing the sparkline exactly as before inside `sparklineHtml`. Nothing else is wired by the
page: no listener, no state, no redraw call. `d4.js` does it in `charted()`; the State section of the new entity page does the same per
row ("Name · current value · sparkline"). With `c0` selected (the default) the wrapper is a plain cell and nothing enlarges.

- `p` the entity path (what `/api/v1/history?path=` takes); `l` the label the enlarged chart shows; `s` the series the sparkline draws,
  each `[status name, measurement key, style]` where style is `""`, `"dim"` (a dashed second line) or `"alt"`.
- A row that draws two lines (CPU average and max, network rx and tx) lists both; a chart view plots all of them.
- One series may be wrapped twice on a page (a group's header and its own row): a wrapper's own state keys on path + series + label
  (`IOCharts.viewKey`), a series' identity on path + series (`IOCharts.specKey`, what c3 / c4 / c5 store).

## What the helper does (web/charts/core.js)

- Delegated listeners on `document` (capture): a click on a wrapper never reaches the row's own handler (a click on a sparkline must not
  open the diagnostic page); a chart view says whether it handled the event.
- The data is the shell's own history cache (`ctx.fetch` in `makeChartsCtx`, app.js): the URL the layouts already ask for
  (`/api/v1/history?path=`), so the enlarged chart and the sparkline are drawn from the same answer. A chart's own window (c1) is the
  API's `window=`.
- `IOCharts.Plot`: the one chart renderer (value axis with nice ticks, time axis on the viewer's wall clock, the status's limits as dashed
  lines tagged `data-diagnostic`, gaps where a status went stale, a cursor readout, an optional right axis, optional zoom / pan, an
  optional health strip on the same x scale, decimation per pixel column).
- A MutationObserver on `#detail` calls the view's `sync()` after each redraw; a 1 Hz timer calls `update()`. Enlarged charts live in a
  shadow root under the wrapper's empty `.ch-host` (the detail's `morph()` cannot reach it), on the body, or in a pane the view adds.

## A chart view

`window.IOChartViews[id] = { name, description, order, mount(env), unmount(env), on(kind, event, hit), update(env), sync(env), attrs(spec, key), tip(spec, key), icon(spec, key) }`;
list it in `web/charts/index.json`. `on` gets `click | contextmenu | pointerover | pointerout | pointerdown | keydown` and `hit = { el, spec, key, real }`
(`real` is the innermost node, shadow DOM included). `attrs` adds attributes to the wrapper from the view's own state (c2's `data-open`
is how the layout stays in step with the unfolded state; the state is regenerated into the markup on every draw).

## The one server change

`POST /api/v1/settings {"dashboard": [...]}` (c4): the page's pinned board is stored whole in settings.toml as a JSON string (at most 60
tiles, 32 KB) and read back by `GET /api/v1/settings`. It is the only way the board can follow the person to another browser or port, and
the API refused any unknown setting before, so it needed the host. `docs/api-v1.md` "Settings", `tests/test_history.py`.
