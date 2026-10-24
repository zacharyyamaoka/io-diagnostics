#!/usr/bin/env python3
"""The numbers behind the health-cell palette (detail/health.js PALETTE: Atlassian Statuspage's colours): WCAG contrast of every cell colour
against the page background, and how far apart the greys are (CIE76 delta E, and the contrast ratio between two cells).

    /usr/bin/python3 /home/bam/io-diagnostics-lanes/diagnostics-variants/docs/health_palettes.py [--json]

It reads the palette out of detail/health.js itself (the lines between `PALETTE = {` and its closing `};`), so the numbers can
never describe different colours from the ones drawn. An rgba value is composited over the page
background first, as the browser does. Stdlib only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HEALTH = Path(__file__).resolve().parent.parent / "io_diagnostics" / "web" / "detail" / "health.js"
# the detail pane's background in the default skin (shadcn): --io-surface-alt, light and dark (the page reports them)
BACKGROUND = {"light": "#fafafa", "dark": "#09090b"}


def palettes() -> dict:
    text = HEALTH.read_text()
    block = text[text.index("const PALETTE = {"):]
    block = block[: block.index("\n  };") + 4]
    spec: dict = {"name": "Atlassian Statuspage"}
    for mode in ("light", "dark"):
        m = re.search(rf"{mode}: \{{([^}}]*)\}}", block)
        spec[mode] = dict(re.findall(r"(\w+): '([^']*)'", m.group(1)))
    return {"statuspage": spec}


def rgb(color: str, over: tuple[float, float, float] | None = None) -> tuple[float, float, float]:
    color = color.strip()
    if color == "transparent":
        return over if over is not None else (0, 0, 0)
    if color.startswith("#"):
        h = color[1:]
        return tuple(int(h[i: i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    m = re.match(r"rgba?\(([^)]*)\)", color)
    parts = [float(x) for x in m.group(1).split(",")]
    if len(parts) == 4 and over is not None:
        a = parts[3]
        return tuple(parts[i] * a + over[i] * (1 - a) for i in range(3))  # type: ignore[return-value]
    return tuple(parts[:3])  # type: ignore[return-value]


def luminance(c: tuple[float, float, float]) -> float:
    def lin(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def lab(c: tuple[float, float, float]) -> tuple[float, float, float]:
    def lin(v: float) -> float:
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(v) for v in c)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116  # noqa: E731
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = lab(a), lab(b)
    return sum((x - y) ** 2 for x, y in zip(la, lb)) ** 0.5


def report() -> dict:
    result: dict = {}
    for pid, spec in palettes().items():
        result[pid] = {"name": spec["name"]}
        for mode in ("light", "dark"):
            bg = rgb(BACKGROUND[mode])
            cells = {level: rgb(value, bg) for level, value in spec[mode].items() if level in ("ok", "warn", "error", "stale", "unknown")}
            if spec[mode].get("tick"):
                cells["ok"] = rgb(spec[mode]["tick"], bg)   # hc5: OK is only a thin baseline tick: its colour is the tick's
            grey = [name for name in ("ok", "stale", "unknown") if name in cells]
            result[pid][mode] = {
                "background": BACKGROUND[mode],
                "contrast": {level: round(contrast(c, bg), 2) for level, c in cells.items()},
                "apart": {f"{a}/{b}": {"ratio": round(contrast(cells[a], cells[b]), 2), "dE": round(delta_e(cells[a], cells[b]), 1)}
                          for i, a in enumerate(grey) for b in grey[i + 1:]},
            }
    return result


if __name__ == "__main__":
    data = report()
    if "--json" in sys.argv:
        print(json.dumps(data, indent=1))
    else:
        for pid, entry in data.items():
            print(f"{pid} {entry['name']}")
            for mode in ("light", "dark"):
                e = entry[mode]
                print(f"  {mode:5} on {e['background']}  contrast {e['contrast']}  greys apart {e['apart']}")
