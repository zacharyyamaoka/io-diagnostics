#!/usr/bin/env python3
"""Build the 20 RobotDiagnostics logo proposals and their gallery (round 3, 2026-10-23).

Writes, for every proposal NN-<slug>:
  brand/logos/NN-<slug>.svg               full-colour mark, 64 grid, works 16-256 px on light and dark
  brand/logos/NN-<slug>-symbolic.svg      22 px monochrome top-bar icon (GNOME / Ayatana AppIndicator)
  brand/logos/states/NN-<slug>-<state>.svg and NN-<slug>-<state>-symbolic.svg   warn / error / stale
and the self-contained gallery reports/media/robotdiagnostics-logos-2026-10-23.html.

WHY symbolic files hold only <rect>/<circle>/<path> with a fill and no stroke: GTK (and GNOME Shell's St, which
reuses its loader) recolours a "-symbolic.svg" by injecting `rect,circle,path { fill: <fg> !important }` plus
`.warning` / `.error` classes. A stroked path would be filled solid and an <ellipse>/<line> would keep its own
colour, so every line here is a filled outline and every state colour is a class. `check_symbolic` enforces it
and the build renders each file through Gtk.IconInfo.load_symbolic to prove it on the real loader.

WHY the brand accent is teal and never red: the app's rule is quiet neutral with colour reserved for problems
(red = error, amber = warn), and a red cross on white is the protected Red Cross emblem.

Run: python3 /home/bam/io-diagnostics/brand/build_logos.py
"""

from __future__ import annotations

import base64
import html
import math
import pathlib
import re
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent
LOGOS = ROOT / "logos"
STATES_DIR = LOGOS / "states"
REPORT = pathlib.Path("/home/bam/io-diagnostics/reports/media/robotdiagnostics-logos-2026-10-23.html")
SHOTS = pathlib.Path("/home/bam/io-diagnostics/reports/media/round3")

INK = "#1f2328"
EDGE = "#c4cad2"
SOFT = "#f2f3f5"
TEAL = "#11867a"
TEAL_D = "#0b5c54"
TEAL_L = "#d2eee9"
TEAL_GLOW = "#5fe0cf"
TILE_DARK = "#11161b"
TILE_EDGE = "#3d4650"
SYM_FILL = "#2e3436"  # Adwaita's symbolic ink; GTK replaces it with the theme's foreground

STATES = ("ok", "warn", "error", "stale")
LIGHT = {"ok": INK, "warn": "#b35c00", "error": "#c00000", "stale": "#8c8c8c"}      # features on a white plate
ONDARK = {"ok": "#ffffff", "warn": "#ffb000", "error": "#ff4d4d", "stale": "#7d7d7d"}  # features on a black tile
GLOW = {"ok": TEAL_GLOW, "warn": "#ffb000", "error": "#ff4d4d", "stale": "#6e7781"}   # lit features on a screen
MOOD = {"ok": "flat", "warn": "frown", "error": "zigzag", "stale": "closed"}         # the pain-scale ladder


# ---------------------------------------------------------------- geometry

def fmt(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def P(x: float, y: float) -> str:
    return f"{fmt(x)},{fmt(y)}"


def circle(cx, cy, r, attrs=""):
    return f'<circle cx="{fmt(cx)}" cy="{fmt(cy)}" r="{fmt(r)}"{attrs}/>'


def rect(x, y, w, h, rx=0.0, attrs=""):
    corner = f' rx="{fmt(rx)}"' if rx else ""
    return f'<rect x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}"{corner}{attrs}/>'


def path(d, attrs=""):
    return f'<path d="{d}"{attrs}/>'


def linspace(a, b, n):
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def circle_d(cx, cy, r):
    return f"M{P(cx - r, cy)}a{fmt(r)},{fmt(r)} 0 1 0 {fmt(2 * r)},0a{fmt(r)},{fmt(r)} 0 1 0 {fmt(-2 * r)},0Z"


def circle_ring_d(cx, cy, r, width):
    return circle_d(cx, cy, r) + circle_d(cx, cy, r - width)


def stroke_d(points, width, cap0="round", cap1="round"):
    """A polyline drawn as a filled outline (mitred joins, round or butt ends) - a stroke a symbolic icon can carry."""
    half = width / 2
    count = len(points)
    normals = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0) or 1.0
        normals.append((-(y1 - y0) / length, (x1 - x0) / length))
    left, right = [], []
    for index, (x, y) in enumerate(points):
        if index == 0 or index == count - 1:
            nx, ny = normals[0] if index == 0 else normals[-1]
            scale = 1.0
        else:
            (ax, ay), (bx, by) = normals[index - 1], normals[index]
            sx, sy = ax + bx, ay + by
            length = math.hypot(sx, sy) or 1.0
            nx, ny = sx / length, sy / length
            scale = 1 / max(0.35, nx * ax + ny * ay)
        left.append((x + nx * half * scale, y + ny * half * scale))
        right.append((x - nx * half * scale, y - ny * half * scale))
    d = "M" + P(*left[0]) + "".join("L" + P(*p) for p in left[1:])
    d += (f"A{fmt(half)},{fmt(half)} 0 0 0 {P(*right[-1])}" if cap1 == "round" else "L" + P(*right[-1]))
    d += "".join("L" + P(*p) for p in reversed(right[:-1]))
    d += (f"A{fmt(half)},{fmt(half)} 0 0 0 {P(*left[0])}" if cap0 == "round" else "")
    return d + "Z"


def _unit(x, y):
    length = math.hypot(x, y) or 1.0
    return x / length, y / length


def rpoly_d(verts, radii):
    """A closed polygon with a circular fillet of radius radii[i] at every vertex (convex or concave)."""
    count = len(verts)
    want = []
    for i in range(count):
        (px, py), (vx, vy), (nx, ny) = verts[i - 1], verts[i], verts[(i + 1) % count]
        ux, uy = _unit(px - vx, py - vy)
        wx, wy = _unit(nx - vx, ny - vy)
        angle = math.acos(max(-1.0, min(1.0, ux * wx + uy * wy)))
        want.append(radii[i] / math.tan(angle / 2) if radii[i] > 0 and 1e-6 < angle < math.pi - 1e-6 else 0.0)
    tangent = list(want)
    for i in range(count):  # two fillets on one edge never overlap
        j = (i + 1) % count
        length = math.dist(verts[i], verts[j])
        if tangent[i] + tangent[j] > length:
            factor = length / (tangent[i] + tangent[j])
            tangent[i], tangent[j] = min(tangent[i], want[i] * factor), min(tangent[j], want[j] * factor)
    pieces = []
    for i in range(count):
        (px, py), (vx, vy), (nx, ny) = verts[i - 1], verts[i], verts[(i + 1) % count]
        ux, uy = _unit(px - vx, py - vy)
        wx, wy = _unit(nx - vx, ny - vy)
        angle = math.acos(max(-1.0, min(1.0, ux * wx + uy * wy)))
        dist = tangent[i]
        radius = dist * math.tan(angle / 2) if dist else 0.0
        cross = (vx - px) * (ny - vy) - (vy - py) * (nx - vx)
        pieces.append(((vx + ux * dist, vy + uy * dist), (vx + wx * dist, vy + wy * dist), radius, 1 if cross > 0 else 0))
    d = "M" + P(*pieces[-1][1])
    for start, end, radius, sweep in pieces:
        d += "L" + P(*start)
        if radius > 1e-6:
            d += f"A{fmt(radius)},{fmt(radius)} 0 0 {sweep} {P(*end)}"
    return d + "Z"


def signed_area(verts):
    return sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(verts, verts[1:] + verts[:1])) / 2


def inset(verts, radii, width):
    """The same rounded polygon moved inward by `width`: edges offset exactly, convex fillets shrink, concave grow."""
    sign = 1 if signed_area(verts) > 0 else -1
    count = len(verts)
    lines = []
    for i in range(count):
        (x0, y0), (x1, y1) = verts[i], verts[(i + 1) % count]
        ex, ey = _unit(x1 - x0, y1 - y0)
        nx, ny = -ey * sign, ex * sign
        lines.append(((x0 + nx * width, y0 + ny * width), (ex, ey)))
    new_verts, new_radii = [], []
    for i in range(count):
        (p1, e1), (p2, e2) = lines[i - 1], lines[i]
        denom = e1[0] * e2[1] - e1[1] * e2[0]
        t = ((p2[0] - p1[0]) * e2[1] - (p2[1] - p1[1]) * e2[0]) / denom
        new_verts.append((p1[0] + e1[0] * t, p1[1] + e1[1] * t))
        convex = denom * sign > 0
        new_radii.append(max(0.0, radii[i] - width) if convex else radii[i] + width)
    return new_verts, new_radii


def ring_d(verts, radii, width):
    return rpoly_d(verts, radii) + rpoly_d(*inset(verts, radii, width))


def box(x, y, w, h):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def rr_d(x, y, w, h, r):
    return rpoly_d(box(x, y, w, h), [r] * 4)


def rr_ring_d(x, y, w, h, r, width):
    return ring_d(box(x, y, w, h), [r] * 4, width)


def large_flag(center, start, end, sweep):
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    delta = (a1 - a0) % (2 * math.pi) if sweep else (a0 - a1) % (2 * math.pi)
    return 1 if delta > math.pi else 0


def heart_d(cx, bottom, half_diag, width=0.0):
    """A heart = a square on its corner plus a semicircle on each upper edge; with width, its exact inward offset."""
    hd = half_diag
    rr = hd / math.sqrt(2)
    m1, m2 = (cx - hd / 2, bottom - 1.5 * hd), (cx + hd / 2, bottom - 1.5 * hd)
    if not width:
        b, lc, top, rc, r = (cx, bottom), (cx - hd, bottom - hd), (cx, bottom - 2 * hd), (cx + hd, bottom - hd), rr
    else:
        k = width / math.sqrt(2)
        r = rr - width
        b = (cx, bottom - width * math.sqrt(2))
        lc, rc = (cx - hd + k, bottom - hd - k), (cx + hd - k, bottom - hd - k)
        top = (cx, m1[1] - math.sqrt(r * r - (hd / 2) ** 2))
    f1, f2 = large_flag(m1, lc, top, 1), large_flag(m2, top, rc, 1)
    return (f"M{P(*b)}L{P(*lc)}A{fmt(r)},{fmt(r)} 0 {f1} 1 {P(*top)}"
            f"A{fmt(r)},{fmt(r)} 0 {f2} 1 {P(*rc)}Z")


def arc_pts(cx, cy, r, a0, a1, n):
    return [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in linspace(a0, a1, n)]


def cubic_pts(p0, p1, p2, p3, n=24):
    out = []
    for t in linspace(0, 1, n):
        u = 1 - t
        out.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return out


def densify(points, step=0.4):
    out = [points[0]]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        n = max(1, int(math.dist((x0, y0), (x1, y1)) / step))
        out += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(1, n + 1)]
    return out


def seg_dist(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy)))
    return math.dist(p, (ax + dx * t, ay + dy * t))


# ---------------------------------------------------------------- the face

def mood_line(s, r, mood):
    """The line joining the eyes, relative to the face centre, for each mood of the ladder."""
    if mood == "flat":
        return [(-s, 0.0), (s, 0.0)]
    if mood in ("smile", "frown"):
        depth = r * 1.15 * (1 if mood == "smile" else -1)
        return [(x, depth * (1 - (x / s) ** 2)) for x in linspace(-s, s, 17)]
    if mood == "zigzag":
        a = r * 0.9
        return list(zip(linspace(-s, s, 6), [0.0, -a, a, -a, a, 0.0]))
    if mood == "wave":
        return [(x, -r * 0.6 * math.sin(math.pi * (x + s) / s)) for x in linspace(-s, s, 25)]
    raise ValueError(mood)


def face_el(cx, cy, s, r, t, mood, attrs=""):
    """Two eyes joined by a line, as filled elements. 'closed' (stale) shuts the eyes and breaks the line."""
    if mood == "closed":
        ew, eh = r * 2.4, max(t * 1.4, r * 0.75)
        dash = 2 * (s - 1.2 * r) * 0.42
        return (rect(cx - s - ew / 2, cy - eh / 2, ew, eh, eh / 2, attrs)
                + rect(cx + s - ew / 2, cy - eh / 2, ew, eh, eh / 2, attrs)
                + rect(cx - dash / 2, cy - t / 2, dash, t, t / 2, attrs))
    if mood == "flat":
        line = rect(cx - s, cy - t / 2, 2 * s, t, t / 2, attrs)
    else:
        line = path(stroke_d([(cx + x, cy + y) for x, y in mood_line(s, r, mood)], t), attrs)
    return line + circle(cx - s, cy, r, attrs) + circle(cx + s, cy, r, attrs)


def _trim(points, center, radius):
    """Drop the start of a polyline that lies inside a circle; the first kept point sits on the circle."""
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        if math.dist(b, center) >= radius:
            lo, hi = 0.0, 1.0
            for _ in range(40):
                mid = (lo + hi) / 2
                q = (a[0] + (b[0] - a[0]) * mid, a[1] + (b[1] - a[1]) * mid)
                lo, hi = (mid, hi) if math.dist(q, center) < radius else (lo, mid)
            return [(a[0] + (b[0] - a[0]) * hi, a[1] + (b[1] - a[1]) * hi)] + points[i + 1:]
    return points


def face_knock(cx, cy, s, r, t, mood):
    """The same face as subpaths that never overlap, for cutting out of a solid shape with fill-rule evenodd.
    WHY trim to sqrt(r^2 - (t/2)^2): a butt-ended line whose corners sit on the eye's edge neither overlaps the eye
    (which evenodd would punch back in) nor leaves a gap."""
    if mood == "closed":
        ew, eh = r * 2.4, max(t * 1.4, r * 0.75)
        dash = 2 * (s - 1.2 * r) * 0.42
        return (rr_d(cx - s - ew / 2, cy - eh / 2, ew, eh, eh / 2) + rr_d(cx + s - ew / 2, cy - eh / 2, ew, eh, eh / 2)
                + rr_d(cx - dash / 2, cy - t / 2, dash, t, t / 2))
    inner = math.sqrt(r * r - (t / 2) ** 2)
    pts = densify([(cx + x, cy + y) for x, y in mood_line(s, r, mood)], 0.25)
    pts = _trim(pts, (cx - s, cy), inner)
    pts = list(reversed(_trim(list(reversed(pts)), (cx + s, cy), inner)))
    if mood != "flat":
        pts = pts[:1] + pts[1:-1][::3] + pts[-1:]
    return circle_d(cx - s, cy, r) + circle_d(cx + s, cy, r) + stroke_d(pts, t, "butt", "butt")


def fill(color):
    return f' fill="{color}"'


# ---------------------------------------------------------------- the 20 proposals
# Each builder returns the inner SVG for the 64 grid (sym=False) or the 22 grid (sym=True).

def b01(st, sym, uid):
    m = MOOD[st]
    if sym:
        return path(rr_ring_d(1, 3, 20, 16, 6, 1.8), ' fill-rule="evenodd"') + face_el(11, 11, 4.6, 2.1, 1.5, m)
    return (rect(5, 11, 54, 42, 17, f' fill="#ffffff" stroke="{EDGE}" stroke-width="2"')
            + face_el(32, 32, 13, 5.6, 3.4, m, fill(LIGHT[st])))


PAIN = {"ok": "smile", "warn": "frown", "error": "zigzag", "stale": "closed"}


def b02(st, sym, uid):
    m = PAIN[st]
    if sym:
        return path(circle_ring_d(11, 11, 10, 1.8), ' fill-rule="evenodd"') + face_el(11, 10.3, 4.1, 1.9, 1.4, m)
    return (circle(32, 32, 26.5, f' fill="#ffffff" stroke="{LIGHT[st]}" stroke-width="3.2"')
            + face_el(32, 29.5, 11, 4.8, 3, m, fill(LIGHT[st])))


def cross_verts(c, a, length):
    return [(c - a, c - length), (c + a, c - length), (c + a, c - a), (c + length, c - a), (c + length, c + a),
            (c + a, c + a), (c + a, c + length), (c - a, c + length), (c - a, c + a), (c - length, c + a),
            (c - length, c - a), (c - a, c - a)]


def b03(st, sym, uid):
    m = MOOD[st]
    if sym:
        verts = cross_verts(11, 4.6, 10.2)
        d = rpoly_d(verts, [2, 2, 0.8] * 4) + face_knock(11, 11, 5.6, 1.85, 1.3, m)
        return path(d, ' fill-rule="evenodd"')
    edge = TEAL if st == "ok" else LIGHT[st]
    return (path(rpoly_d(cross_verts(32, 11, 28), [6, 6, 3] * 4),
                 f' fill="#ffffff" stroke="{edge}" stroke-width="3.2" stroke-linejoin="round"')
            + face_el(32, 32, 14.5, 5, 3, m, fill(LIGHT[st])))


SHIELD = [(10, 9), (54, 9), (54, 26), (32, 58), (10, 26)]


def b04(st, sym, uid):
    m = MOOD[st]
    if sym:
        verts = [(3, 2.5), (19, 2.5), (19, 8.5), (11, 20.5), (3, 8.5)]
        return path(ring_d(verts, [2, 2, 10, 1.5, 10], 1.7), ' fill-rule="evenodd"') + face_el(11, 9.3, 3.9, 1.75, 1.3, m)
    edge = TEAL_D if st == "ok" else LIGHT[st]
    return (path(rpoly_d(SHIELD, [5, 5, 30, 5, 30]), f' fill="{TEAL_L}" stroke="{edge}" stroke-width="3.2"')
            + face_el(32, 28, 11, 4.8, 3, m, fill(LIGHT[st])))


def b05(st, sym, uid):
    m = MOOD[st]
    if sym:
        return path(heart_d(11, 20.3, 7.6) + face_knock(11, 10.6, 3.7, 1.6, 1.15, m), ' fill-rule="evenodd"')
    body = {"ok": TEAL, "warn": "#b35c00", "error": "#c00000", "stale": "#9aa0a6"}[st]
    return path(heart_d(32, 56, 21.3), fill(body)) + face_el(32, 31, 12, 5, 3, m, fill("#ffffff"))


def b06(st, sym, uid):
    m = MOOD[st]
    if sym:
        return (path(rr_ring_d(1, 5.5, 20, 11, 5.5, 1.6), ' fill-rule="evenodd"')
                + rect(10.3, 7.1, 1.4, 2.2) + rect(10.3, 12.7, 1.4, 2.2) + face_el(11, 11, 5, 1.8, 1.3, m))
    return (path("M32,19H19A13,13 0 0 0 19,45H32Z", ' fill="#ffffff"')
            + path("M32,19H45A13,13 0 0 1 45,45H32Z", fill(TEAL_L))
            + rect(31, 19, 2, 26, 0, fill(TEAL_D))
            + rect(6, 19, 52, 26, 13, f' fill="none" stroke="{TEAL_D}" stroke-width="2.4"')
            + face_el(32, 32, 13, 5, 3, m, fill(LIGHT[st])))


SCOPE = {
    "ok": [(-18, 0), (-6, 0), (-3.5, -7), (0, 7), (2.5, -3), (4.5, 0), (18, 0)],
    "warn": [(x, -4.5 * math.sin(2 * math.pi * (x + 18) / 24)) for x in linspace(-18, 18, 37)],
    "error": [(-18, 0), (-10, 0), (-7, -15), (-3, 13), (1, -15), (5, 13), (8, 0), (18, 0)],
}


def b07(st, sym, uid):
    if sym:
        k, cx, cy = 5.8 / 18, 11, 11
        out = path(rr_ring_d(1, 1, 20, 20, 4.5, 1.6), ' fill-rule="evenodd"')
        out += circle(cx - 5.8, cy, 1.7) + circle(cx + 5.8, cy, 1.7)
        if st == "stale":
            return out + "".join(rect(x, cy - 0.6, 1.4, 1.2) for x in (7.3, 10.3, 13.3))
        return out + path(stroke_d([(cx + x * k, cy + y * k) for x, y in SCOPE[st]], 1.25))
    col = GLOW[st]
    out = rect(4, 4, 56, 56, 11, f' fill="{TILE_DARK}" stroke="{TILE_EDGE}" stroke-width="1.5"')
    out += "".join(f'<path d="M{v},8V56M8,{v}H56" stroke="#24303b" stroke-width="1" fill="none"/>' for v in (18, 32, 46))
    out += circle(14, 32, 4.3, fill(col)) + circle(50, 32, 4.3, fill(col))
    if st == "stale":
        return out + f'<path d="M18,32H46" stroke="{col}" stroke-width="2.6" stroke-dasharray="3 3.5" fill="none"/>'
    pts = " ".join(P(32 + x, 32 + y) for x, y in SCOPE[st])
    return out + (f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.6" '
                  'stroke-linejoin="round" stroke-linecap="round"/>')


JIT = [0, -1.0, 0.9, -0.7, 1.0, -0.5, 0.9, -0.9, 0.6, -0.7, 0]
JIT7 = [0, -0.5, 0.45, -0.4, 0.45, -0.4, 0]


def spark(st, s, depth, jit):
    xs = linspace(-s, s, len(jit))
    if st == "ok":
        return [(x, depth * (1 - (x / s) ** 2) + j) for x, j in zip(xs, jit)]
    if st == "warn":
        return [(x, j * 1.8) for x, j in zip(xs, jit)]
    return [(x, -depth * 0.85 * (1 - (x / s) ** 2) + j * 2.2) for x, j in zip(xs, jit)]


def b08(st, sym, uid):
    if sym:
        cx, cy = 11, 10
        out = path(circle_ring_d(11, 11, 10, 1.6), ' fill-rule="evenodd"')
        if st == "stale":
            return out + face_el(cx, cy, 5, 1.6, 1.15, "closed")
        out += path(stroke_d([(cx + x, cy + y) for x, y in spark(st, 5, 2.3, JIT7)], 1.15))
        return out + circle(cx - 5, cy, 1.55) + circle(cx + 5, cy, 1.95)
    col = LIGHT[st]
    cx, cy = 32, 27
    out = circle(32, 32, 28, f' fill="{SOFT}" stroke="{EDGE}" stroke-width="1.5"')
    if st == "stale":
        out += f'<path d="M{cx - 15},{cy}H{cx + 15}" stroke="{col}" stroke-width="2.6" stroke-dasharray="3 3" fill="none"/>'
        return out + circle(cx - 15, cy, 4.5, fill(col)) + circle(cx + 15, cy, 4.2, f' fill="{SOFT}" stroke="{col}" stroke-width="2.2"')
    pts = " ".join(P(cx + x, cy + y) for x, y in spark(st, 15, 7, JIT))
    out += f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2.6" stroke-linejoin="round"/>'
    return out + circle(cx - 15, cy, 4.5, fill(col)) + circle(cx + 15, cy, 5.2, fill(TEAL if st == "ok" else col))


BAR = {"ok": 1.0, "warn": 0.62, "error": 0.28, "stale": 0.0}


def b09(st, sym, uid):
    frac = BAR[st]
    if sym:
        out = path(rr_ring_d(1, 1, 20, 20, 5.5, 1.6), ' fill-rule="evenodd"')
        out += rect(5.6, 9.9, 10.8, 2.2, 1.1, ' opacity="0.35"')
        if frac:
            out += rect(5.6, 9.9, 10.8 * frac, 2.2, 1.1)
        if st == "stale":
            return out + rect(3.2, 10.2, 4.8, 1.6, 0.8) + rect(14, 10.2, 4.8, 1.6, 0.8)
        return out + circle(5.6, 11, 2) + circle(16.4, 11, 2)
    col = LIGHT[st]
    out = rect(5, 5, 54, 54, 15, f' fill="#ffffff" stroke="{EDGE}" stroke-width="2"')
    out += rect(17, 29, 30, 6, 3, fill("#e4e7eb"))
    if frac:
        out += rect(17, 29, 30 * frac, 6, 3, fill(col))
    if st == "stale":
        return out + rect(10.5, 30, 13, 4, 2, fill(col)) + rect(40.5, 30, 13, 4, 2, fill(col))
    return out + circle(17, 32, 5.5, fill(col)) + circle(47, 32, 5.5, fill(col))


def rd_letters():
    """R and D as centre-line polylines on the 64 grid (stroke 5)."""
    stem_r = [(13, 16), (13, 48)]
    bowl_r = ([(13, 16), (22, 16)] + arc_pts(22, 24, 8, -90, 0, 12)[1:] + [(30, 26)]
              + arc_pts(22, 26, 8, 0, 90, 12)[1:] + [(13, 34)])
    leg_r = [(21, 34), (30, 48)]
    stem_d = [(37, 16), (37, 48)]
    bowl_d = ([(37, 16), (42, 16)] + arc_pts(42, 26, 10, -90, 0, 14)[1:] + [(52, 38)]
              + arc_pts(42, 38, 10, 0, 90, 14)[1:] + [(37, 48)])
    return [stem_r, bowl_r, leg_r, stem_d, bowl_d]


def b10(st, sym, uid):
    """The face threads through R and D: its eyes sit in the two counters, the letters part to let the line by."""
    k = 22 / 64 if sym else 1.0
    letter_w, eye_r, line_t, clear = (1.75, 1.4, 1.05, 0.55) if sym else (5, 3.8, 2.6, 1.6)
    e1, e2, ly = (21.5 * k, 25 * k), (44.5 * k, 25 * k), 25 * k
    s, cx = (e2[0] - e1[0]) / 2, (e1[0] + e2[0]) / 2
    m = MOOD[st]
    line = [(cx + x, ly + y) for x, y in mood_line(s, eye_r, m if m != "closed" else "flat")]
    line = densify(line, 0.3 * k)
    reach = line_t / 2 + clear + letter_w / 2
    pieces = []
    for poly in rd_letters():
        pts = densify([(x * k, y * k) for x, y in poly], 0.3 * k)
        run = []
        for p in pts:
            near = e1[0] < p[0] < e2[0] and min(seg_dist(p, a, b) for a, b in zip(line, line[1:])) < reach
            if near:
                if len(run) > 1:
                    pieces.append(run)
                run = []
            else:
                run.append(p)
        if len(run) > 1:
            pieces.append(run)
    letters = "".join(stroke_d(run, letter_w) for run in pieces)
    if sym:
        return path(letters) + face_el(cx, ly, s, eye_r, line_t, m)
    col = TEAL if st == "ok" else LIGHT[st]
    return (rect(4, 4, 56, 56, 14, f' fill="#ffffff" stroke="{EDGE}" stroke-width="1.5"')
            + path(letters, fill(INK)) + face_el(cx, ly, s, eye_r, line_t, m, fill(col)))


def b11(st, sym, uid):
    m = MOOD[st]
    if sym:
        brackets = (rect(2, 4, 1.8, 14) + rect(2, 4, 4, 1.8) + rect(2, 16.2, 4, 1.8)
                    + rect(18.2, 4, 1.8, 14) + rect(16, 4, 4, 1.8) + rect(16, 16.2, 4, 1.8))
        return brackets + face_el(11, 11, 4, 1.8, 1.4, m)
    white = fill("#ffffff")
    brackets = (rect(13, 16, 4.5, 32, 0, white) + rect(13, 16, 9, 4.5, 0, white) + rect(13, 43.5, 9, 4.5, 0, white)
                + rect(46.5, 16, 4.5, 32, 0, white) + rect(42, 16, 9, 4.5, 0, white) + rect(42, 43.5, 9, 4.5, 0, white))
    return (rect(4, 4, 56, 56, 9, ' fill="#000000" stroke="#4a4a4a" stroke-width="1.5"') + brackets
            + face_el(32, 32, 9.5, 4, 2.8, m, fill(ONDARK[st])))


PIX16 = {"ok": [7, 7, 7, 7], "warn": [8, 7, 7, 8], "error": [6, 8, 6, 8]}
PIX22 = {"ok": [9, 9, 9], "warn": [10, 8, 10], "error": [7, 11, 7]}


def b12(st, sym, uid):
    """Pixel face: every edge on the pixel grid, so 16 px and 22 px stay crisp."""
    if sym:
        out = path(rr_ring_d(1, 1, 20, 20, 3, 2), ' fill-rule="evenodd"')
        if st == "stale":
            return out + rect(4, 10, 4, 2) + rect(14, 10, 4, 2) + rect(10, 10, 2, 2)
        return out + rect(4, 8, 4, 4) + rect(14, 8, 4, 4) + "".join(rect(8 + 2 * i, y, 2, 2) for i, y in enumerate(PIX22[st]))
    col = fill(ONDARK[st])
    out = rect(2, 2, 60, 60, 8, ' fill="#000000" stroke="#4a4a4a" stroke-width="1.5"')
    if st == "stale":
        return out + rect(12, 32, 12, 4, 0, col) + rect(40, 32, 12, 4, 0, col) + rect(28, 32, 8, 4, 0, col)
    out += rect(12, 24, 12, 12, 0, col) + rect(40, 24, 12, 12, 0, col)
    return out + "".join(rect(24 + 4 * i, 4 * y, 4, 4, 0, col) for i, y in enumerate(PIX16[st]))


# WHY mid-tone amber and red: this mark has no plate, so one colour must read on white and on a dark panel alike
ISA_GREY = {"ok": "#8a9099", "warn": "#d47a00", "error": "#e0383e", "stale": "#aab0b8"}


def b13(st, sym, uid):
    """ISA-101 grey gauge: the normal mark is literally the normal state - grey, no hue."""
    m = MOOD[st]
    if sym:
        return path(stroke_d(arc_pts(11, 11, 9.6, 125, 415, 64), 1.8)) + face_el(11, 10.6, 4.1, 1.9, 1.4, m)
    col = ISA_GREY[st]
    dash = ' stroke-dasharray="4 5"' if st == "stale" else ""
    arc = arc_pts(32, 32, 25, 125, 415, 2)
    return (f'<path d="M{P(*arc[0])}A25,25 0 1 1 {P(*arc[-1])}" fill="none" stroke="{col}" stroke-width="4.2" '
            f'stroke-linecap="round"{dash}/>' + face_el(32, 31, 11.5, 5, 3.2, m, fill(col)))


PRETTY = {"ok": ("#1fae9c", "#0c6f64"), "warn": ("#f0a23a", "#b35c00"), "error": ("#e5484d", "#a30000"),
          "stale": ("#b4b9bf", "#6e737a")}


def b14(st, sym, uid):
    m = MOOD[st]
    if sym:
        return rect(1, 1, 20, 20, 6, ' opacity="0.35"') + face_el(11, 11, 4.6, 2.1, 1.5, m)
    top, bottom = PRETTY[st]
    return (f'<defs><linearGradient id="t{uid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient>'
            f'<linearGradient id="c{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/>'
            f'<stop offset="1" stop-color="#e6f0ee"/></linearGradient></defs>'
            + rect(4, 4, 56, 56, 15, f' fill="url(#t{uid})"')
            + rect(11, 19.5, 42, 32, 14, ' fill="#05342f" opacity="0.22"')
            + rect(11, 17, 42, 32, 14, f' fill="url(#c{uid})"')
            + face_el(32, 33, 11, 4.6, 2.8, m, fill(INK if st != "stale" else "#7a8088")))


def b15(st, sym, uid):
    m = MOOD[st]
    if sym:
        dots = "".join(circle(x, y, 0.75) for x in (4.6, 17.4) for y in (9.6, 12.4))
        return (f'<g transform="rotate(-35 11 11)">' + path(rr_ring_d(1.5, 6, 19, 10, 5, 1.5), ' fill-rule="evenodd"')
                + dots + face_el(11, 11, 3.6, 1.55, 1.15, m) + "</g>")
    dots = "".join(circle(x, y, 1.1, fill("#d2bfa9")) for x in (9, 13.5, 50.5, 55) for y in (28.5, 35.5))
    return ('<g transform="rotate(-35 32 32)">'
            + rect(3, 20, 58, 24, 12, ' fill="#f2e8dd" stroke="#c4ae97" stroke-width="1.5"')
            + rect(20, 23.5, 24, 17, 4.5, ' fill="#ffffff" stroke="#dccbb9" stroke-width="1.2"')
            + dots + face_el(32, 32, 7, 3.2, 2.1, m, fill(LIGHT[st])) + "</g>")


def b16(st, sym, uid):
    m = MOOD[st]
    if sym:
        return path(rr_d(1, 5, 20, 12, 6) + face_knock(11, 11, 5, 1.9, 1.3, m), ' fill-rule="evenodd"')
    col = GLOW[st]
    glow = "" if st == "stale" else face_el(32, 32, 13, 6.8, 5.6, m, f' fill="{col}" opacity="0.22"')
    return (rect(4, 4, 56, 56, 17, f' fill="#ffffff" stroke="{EDGE}" stroke-width="1.8"')
            + rect(9, 19, 46, 26, 13, fill(TILE_DARK)) + glow + face_el(32, 32, 13, 4.5, 2.6, m, fill(col)))


def b17(st, sym, uid):
    """Stethoscope: the earpieces are the eyes, the spring bridge between them is the line."""
    m = MOOD[st]
    if sym:
        tubes = (stroke_d(cubic_pts((6.6, 4.6), (6.6, 10), (8, 12.6), (11, 13)), 1.4)
                 + stroke_d(cubic_pts((15.4, 4.6), (15.4, 10), (14, 12.6), (11, 13)), 1.4)
                 + stroke_d([(11, 13), (11, 15.2)], 1.4))
        return path(tubes) + path(circle_ring_d(11, 18, 3, 1.4), ' fill-rule="evenodd"') + face_el(11, 4.6, 4.4, 1.7, 1.2, m)
    col = LIGHT[st]
    return (circle(32, 32, 28, f' fill="{SOFT}" stroke="{EDGE}" stroke-width="1.5"')
            + f'<path d="M21,16C21,31 25,37 32,38M43,16C43,31 39,37 32,38M32,38V43" fill="none" stroke="{TEAL}" '
              'stroke-width="3.6" stroke-linecap="round"/>'
            + circle(32, 49, 6, f' fill="#ffffff" stroke="{INK}" stroke-width="3"')
            + face_el(32, 16, 11, 4.2, 2.8, m, fill(col)))


PORT = [(6, 17), (58, 17), (51, 47), (13, 47)]


def b18(st, sym, uid):
    """A D-sub port: two pins joined by a jumper - the IO in IO diagnostics."""
    m = MOOD[st]
    if sym:
        verts = [(1.5, 5), (20.5, 5), (17, 17), (5, 17)]
        return path(ring_d(verts, [2.6] * 4, 1.7), ' fill-rule="evenodd"') + face_el(11, 10.8, 4.6, 1.75, 1.3, m)
    cavity = inset(PORT, [7] * 4, 5)
    return (path(rpoly_d(PORT, [7] * 4), f' fill="#ffffff" stroke="{INK}" stroke-width="3.4"')
            + path(rpoly_d(*cavity), fill("#eceef1"))
            + face_el(32, 31.5, 12, 4.6, 3, m, fill(LIGHT[st] if st != "ok" else INK)))


def b19(st, sym, uid):
    """A check-in bubble: 'how are you feeling?' as the question the tool keeps asking its devices."""
    m = MOOD[st]
    if sym:
        verts = [(1.5, 2), (20.5, 2), (20.5, 15.5), (12, 15.5), (5.5, 20.5), (7, 15.5), (1.5, 15.5)]
        outer = rpoly_d(verts, [5, 5, 5, 0.8, 0.9, 0.8, 5])
        inner = rpoly_d(*inset(box(1.5, 2, 19, 13.5), [5] * 4, 1.6))
        return path(outer + inner, ' fill-rule="evenodd"') + face_el(11, 8.8, 4.6, 1.8, 1.3, m)
    verts = [(6, 8), (58, 8), (58, 46), (36, 46), (20, 57), (24, 46), (6, 46)]
    return (path(rpoly_d(verts, [12, 12, 12, 2.5, 2.2, 2.5, 12]),
                 f' fill="#ffffff" stroke="{INK if st == "ok" else LIGHT[st]}" stroke-width="3.2" stroke-linejoin="round"')
            + face_el(32, 27, 12, 4.8, 3, m, fill(LIGHT[st])))


def b20(st, sym, uid):
    """A circuit trace between two vias; an error is an open circuit, not just a red one."""
    if sym:
        out = path(rr_ring_d(1, 1, 20, 20, 4.5, 1.5), ' fill-rule="evenodd"')
        out += path(circle_ring_d(6.2, 11, 2.3, 1.1) + circle_ring_d(15.8, 11, 2.3, 1.1), ' fill-rule="evenodd"')
        if st == "ok":
            return out + rect(8.4, 10.4, 5.2, 1.2)
        if st == "warn":
            return out + path(stroke_d([(8.4, 11), (9.2, 11), (10, 9.3), (12, 9.3), (12.8, 11), (13.6, 11)], 1.2, "butt", "butt"))
        if st == "error":
            return out + rect(8.4, 10.4, 1.4, 1.2) + rect(12.2, 10.4, 1.4, 1.2)
        return out + rect(10.3, 10.4, 1.4, 1.2)
    col = ONDARK[st] if st != "ok" else "#e6edf3"
    out = rect(4, 4, 56, 56, 11, f' fill="{TILE_DARK}" stroke="{TILE_EDGE}" stroke-width="1.5"')
    out += "".join(rect(x, y, 4, 4, 1, fill("#2b343e")) for x in (10, 50) for y in (10, 50))
    out += circle(16, 32, 5.6, f' fill="none" stroke="{col}" stroke-width="3.2"')
    out += circle(48, 32, 5.6, f' fill="none" stroke="{col}" stroke-width="3.2"')
    trace = {"ok": "M23.2,32H40.8", "warn": "M23.2,32H27L30,28.5H34L37,32H40.8",
             "error": "M23.2,32H28.5M35.5,32H40.8", "stale": "M23.2,32H40.8"}[st]
    dash = ' stroke-dasharray="2.5 3"' if st == "stale" else ""
    return out + f'<path d="{trace}" fill="none" stroke="{col}" stroke-width="3.2" stroke-linejoin="round"{dash}/>'


# num, slug, name, idea, distance (1 = closest to the reference, 5 = clearly distinct), distance text,
# status fit, family, builder
PROPOSALS = [
    (1, "calm-line", "Calm Line", "The calm two-dot face on a soft white cushion; the line bends with health.",
     1, "Closest. The same calm two-eyes-and-a-line face on a plain white cushion: generic geometry (two circles "
        "and a bar), no head-and-body silhouette, no Disney artwork.", "Strong", "Face-as-status", b01),
    (2, "pain-scale", "Pain Scale", "A chart face whose joining line smiles, sags, then grimaces - the 1-10 pain "
     "scale as a status icon.", 2, "Close in spirit. Borrows the pain-scale chart's round outlined face; the "
     "robot itself is gone.", "Strong", "Face-as-status", b02),
    (3, "care-cross", "Care Cross", "A white rounded cross with a teal edge; the face rides the crossbar.",
     3, "Medical container, face as the hook. Teal-edged white so it is never the protected red-on-white "
        "Red Cross emblem.", "Good", "Health container", b03),
    (4, "guard-shield", "Guard Shield", "A soft shield that watches the rig; face high on the crest.",
     4, "Distinct. Shield shape and teal tint; only the face motif carries over.", "Good", "Health container", b04),
    (5, "kind-heart", "Kind Heart", "A teal heart with a white face; the heart changes colour only when something "
     "is wrong.", 3, "Care connotation first; the face is small and generic.", "Good", "Health container", b05),
    (6, "capsule", "Capsule", "A two-tone pill; one eye in each half, the line crosses the seam.",
     3, "A pill, not a robot. The face is the only shared idea.", "Good", "Health container", b06),
    (7, "scope-trace", "Scope Trace", "An oscilloscope screen: the eyes are probe points, the line is the live "
     "trace, calm with one heartbeat.", 4, "Distinct. Reads as an instrument; the face appears only when you look.",
     "Strong", "Face as measurement", b07),
    (8, "sparkline-smile", "Sparkline Smile", "The smile is a sparkline; the right eye is the latest value, "
     "in teal.", 3, "A data chart that happens to smile.", "Good", "Face as measurement", b08),
    (9, "health-bar", "Health Bar", "The line between the eyes is a health bar that drains: full, 62 %, 28 %, "
     "empty.", 4, "A game health bar with eyes.", "Strong", "Face as measurement", b09),
    (10, "rd-monogram", "RD Monogram", "R and D with the face threaded through: an eye in each counter, the "
     "letters part to let the line by.", 5, "Clearly distinct. A lettermark; the face is a detail inside it.",
     "Fair", "Monogram", b10),
    (11, "bracket-face", "Bracket Face", "[•-•] - the face as a terminal token, white on black.",
     4, "Distinct. Terminal glyph; nothing soft or plush about it.", "Good", "Bare Metal", b11),
    (12, "pixel-face", "Pixel Face", "Every edge on the pixel grid, so it is razor sharp at 16 and 22 px.",
     3, "The face idea in 8-bit blocks.", "Strong", "Bare Metal", b12),
    (13, "isa-gauge", "ISA Gauge", "An analog gauge ring in ISA-101 grey: normal has no hue; only a problem "
     "colours it.", 2, "Close in spirit: a round outline with the calm face, but an instrument dial, not a head.",
     "Strong", "Bare Metal", b13),
    (14, "pretty-cushion", "Pretty Cushion", "A soft teal app tile holding a white cushion face; the tile "
     "turns amber or red with trouble.", 2, "Close in spirit to the app-icon reference: soft tile, white face. "
     "No peeking head, no body.", "Strong", "Pretty", b14),
    (15, "bandage", "Bandage", "A tilted adhesive bandage; the face sits on the pad among the perforations.",
     4, "Distinct. A first-aid object with a face.", "Fair", "Own", b15),
    (16, "visor", "Visor", "A robot visor: lit teal eyes and line behind dark glass.", 4,
     "A visor robot, not a plush one: hard surfaces, glowing face.", "Strong", "Own", b16),
    (17, "stethoscope", "Stethoscope", "The earpieces are the eyes and the spring bridge is the line; the tubes "
     "become a chin.", 5, "Clearly distinct. A stethoscope first; the face is a discovery.", "Fair", "Own", b17),
    (18, "port", "Port", "A D-sub connector: two pins and a jumper. The IO in IO diagnostics.", 5,
     "Clearly distinct. Hardware first, the face is a pun.", "Good", "Own", b18),
    (19, "check-in", "Check-in", "A speech bubble asking 'how are you feeling?' with the face inside.", 3,
     "The question the reference character asks, not the character.", "Good", "Own", b19),
    (20, "circuit", "Circuit", "Two vias and a trace on a dark board; an error is an open circuit.", 4,
     "Distinct. A PCB detail that reads as a face.", "Good", "Own", b20),
]

TOP5 = [
    (1, "Calm Line", "The purest form of the face he likes: two eyes and a calm line, almost no decoration. Reads "
        "as a face at 22 px in the top bar, and its four moods (flat, sag, grimace, eyes shut) differ by shape."),
    (7, "Scope Trace", "Says 'diagnostics instrument' before it says 'face', so it ages well and sits furthest from "
        "the reference of the top three. Strongest state ladder: heartbeat, wave, spikes, flatline."),
    (13, "ISA Gauge", "Puts the app's colour rule into the logo itself: grey when normal, coloured only when "
         "something is wrong. The quietest top-bar icon, and it matches Bare Metal."),
    (3, "Care Cross", "The boldest silhouette at 22 px (a solid cross with the face cut out) and an instant "
        "'health' read. Teal-edged white, never the red-on-white emblem."),
    (14, "Pretty Cushion", "The best dock tile: soft and friendly, and the whole tile turns amber or red, so a "
         "problem shows from across the room. Pairs with the Pretty skin. Weakest symbolic of the five."),
]
RUNNER_UP = ("Runner-up: 12 Pixel Face. Its symbolic is the sharpest at true 22 px, but at dock size the square "
             "eyes read as a dumbbell rather than a face.")


# ---------------------------------------------------------------- files

def svg_file(content, size, title, symbolic=False):
    body = f'<g fill="{SYM_FILL}">{content}</g>' if symbolic else content
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f"<title>{html.escape(title)}</title>{body}</svg>\n")


def sym_state(content, state):
    """A problem state recolours the whole icon through GTK's own classes; stale fades it (old data fades)."""
    if state in ("warn", "error"):
        cls = "warning" if state == "warn" else "error"
        return re.sub(r"<(rect|circle|path)\b", rf'<\1 class="{cls}"', content)
    if state == "stale":
        return f'<g opacity="0.5">{content}</g>'
    return content


SYM_TAGS = {"svg", "g", "rect", "circle", "path", "title"}
SYM_ATTRS = {"xmlns", "width", "height", "viewBox", "d", "x", "y", "rx", "cx", "cy", "r", "fill", "fill-rule",
             "opacity", "class", "transform"}


def check_symbolic(text):
    problems = []
    for element in ET.fromstring(text).iter():
        tag = element.tag.split("}")[-1]
        if tag not in SYM_TAGS:
            problems.append(f"<{tag}>")
        problems += [f"{tag}@{name}" for name in element.attrib if name not in SYM_ATTRS]
    return problems


def check_full(text):
    ET.fromstring(text)
    return [word for word in ("<text", "<image", "font", "href=\"http") if word in text]


def write_files():
    LOGOS.mkdir(parents=True, exist_ok=True)
    STATES_DIR.mkdir(parents=True, exist_ok=True)
    problems = {}
    for num, slug, name, *_rest, build in PROPOSALS:
        stem = f"{num:02d}-{slug}"
        for state in STATES:
            full = svg_file(build(state, False, f"{num}{state}"), 64, f"RobotDiagnostics - {name}"
                            + ("" if state == "ok" else f" ({state})"))
            sym = svg_file(sym_state(build(state, True, ""), state), 22, f"RobotDiagnostics - {name} symbolic"
                           + ("" if state == "ok" else f" ({state})"), symbolic=True)
            full_path = LOGOS / f"{stem}.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}.svg"
            sym_path = LOGOS / f"{stem}-symbolic.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}-symbolic.svg"
            full_path.write_text(full)
            sym_path.write_text(sym)
            found = check_full(full) + check_symbolic(sym)
            if found:
                problems[f"{stem}/{state}"] = found
    return problems


# ---------------------------------------------------------------- the real GTK loader

def gtk_proof():
    """Each symbolic file through Gtk.IconInfo.load_symbolic at 22 px, the path GNOME uses to recolour it.
    Returns {(num, state, theme): base64 png} plus the contact sheet path."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, Gtk

    def rgba(text):
        color = Gdk.RGBA()
        color.parse(text)
        return color

    themes = {"dark": ("#ffffff", "#ffb000", "#ff4d4d", "#1d1d1d"), "light": ("#2e3436", "#b35c00", "#c00000", "#fafafa")}
    theme = Gtk.IconTheme.new()
    out = {}
    scale = 4
    cell = 22 * scale + 12
    sheet = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, cell * 8 + 12, cell * len(PROPOSALS) + 12)
    sheet.fill(0x808080ff)
    for row, (num, slug, *_rest) in enumerate(PROPOSALS):
        stem = f"{num:02d}-{slug}"
        for col, (theme_name, (fg, warn, err, bg)) in enumerate(themes.items()):
            for k, state in enumerate(STATES):
                file = LOGOS / f"{stem}-symbolic.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}-symbolic.svg"
                info = theme.lookup_by_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(file))), 22,
                                             Gtk.IconLookupFlags.FORCE_SIZE)
                pix, was_symbolic = info.load_symbolic(rgba(fg), rgba(fg), rgba(warn), rgba(err))
                assert was_symbolic, file
                ok, data = pix.save_to_bufferv("png", [], [])
                out[(num, state, theme_name)] = base64.b64encode(data).decode()
                tile = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 22 * scale, 22 * scale)
                tile.fill(int(bg[1:], 16) << 8 | 0xff)
                big = pix.scale_simple(22 * scale, 22 * scale, GdkPixbuf.InterpType.NEAREST)
                big.composite(tile, 0, 0, 22 * scale, 22 * scale, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
                tile.copy_area(0, 0, 22 * scale, 22 * scale, sheet, 12 + (col * 4 + k) * cell, 12 + row * cell)
    SHOTS.mkdir(parents=True, exist_ok=True)
    sheet_path = SHOTS / "logos-gtk-symbolic-sheet.png"
    sheet.savev(str(sheet_path), "png", [], [])
    return out, sheet_path


# ---------------------------------------------------------------- gallery

def sprite():
    symbols = []
    for num, slug, name, *_rest, build in PROPOSALS:
        for state in STATES:
            symbols.append(f'<symbol id="f{num}-{state}" viewBox="0 0 64 64">{build(state, False, f"{num}{state}")}</symbol>')
            content = sym_state(build(state, True, ""), state)
            content = content.replace('class="warning"', 'class="warning" style="fill:var(--sym-warn,currentColor)"')
            content = content.replace('class="error"', 'class="error" style="fill:var(--sym-error,currentColor)"')
            symbols.append(f'<symbol id="s{num}-{state}" viewBox="0 0 22 22"><g fill="currentColor">{content}</g></symbol>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true">'
            + "".join(symbols) + "</svg>")


def use(ref, size, cls=""):
    return f'<svg class="ic {cls}" width="{size}" height="{size}"><use href="#{ref}"/></svg>'


def ladder(num):
    sizes = "".join(f'<figure>{use(f"f{num}-ok", n)}<figcaption>{n}</figcaption></figure>' for n in (16, 22, 32, 64, 256))
    sym = f'<figure class="sym">{use(f"s{num}-ok", 22)}<figcaption>22 sym</figcaption></figure>'
    return (f'<div class="ladder light">{sizes}{sym}</div>'
            f'<div class="ladder dark">{sizes}{sym}</div>')


def topbar(num, state, label, theme="dark"):
    return (f'<div class="topbar {theme}"><span class="tb-left">Activities</span><span class="tb-clock">Oct 23  10:41</span>'
            f'<span class="tb-right"><span class="chip">{use(f"s{num}-{state}", 22, "tb-ic")}<span>{label}</span></span>'
            '<span class="tb-sys"><i></i><i></i><i></i></span></span></div>')


def dock(num, theme):
    others = "".join('<span class="dock-tile other"></span>' for _ in range(3))
    return (f'<div class="dock {theme}">{others}<span class="dock-tile ours">{use(f"f{num}-ok", 48)}'
            '<b class="running"></b></span><span class="dock-tile other"></span></div>')


def status_set(num, gtk):
    full_l = "".join(f'<figure>{use(f"f{num}-{s}", 64)}<figcaption>{s}</figcaption></figure>' for s in STATES)
    bar = "".join(f'<span class="chip">{use(f"s{num}-{s}", 22, "tb-ic")}<span>{lbl}</span></span>'
                  for s, lbl in zip(STATES, ("IO ✓", "IO 3 ⚠", "IO 1 ✖", "IO ◷")))
    proof = "".join(f'<img src="data:image/png;base64,{gtk[(num, s, "dark")]}" width="22" height="22" alt="{s}">'
                    f'<img class="zoom" src="data:image/png;base64,{gtk[(num, s, "dark")]}" width="66" height="66" alt="">'
                    for s in STATES)
    proof_l = "".join(f'<img src="data:image/png;base64,{gtk[(num, s, "light")]}" width="22" height="22" alt="{s}">'
                      f'<img class="zoom" src="data:image/png;base64,{gtk[(num, s, "light")]}" width="66" height="66" alt="">'
                      for s in STATES)
    return (f'<div class="states"><div class="ladder light st">{full_l}</div><div class="ladder dark st">{full_l}</div></div>'
            f'<div class="topbar dark wide"><span class="tb-left">OK · warn · error · stale</span><span class="tb-right">{bar}</span></div>'
            f'<div class="gtk"><div class="gtk-row dark">{proof}</div><div class="gtk-row light">{proof_l}</div>'
            '<p class="note">Real GTK 3.24 <code>Gtk.IconInfo.load_symbolic</code> output of the shipped <code>-symbolic.svg</code> '
            'files at 22 px (left of each pair) and the same pixels at 3x, nearest-neighbour (right).</p></div>')


def card(entry, gtk):
    num, slug, name, idea, dist, dist_text, fit, family, _build = entry
    pips = "".join(f'<i class="{"on" if i < dist else ""}"></i>' for i in range(5))
    return f"""
<section class="card" id="logo-{num:02d}">
  <header>
    <div class="num">{num:02d}</div>
    <div class="title"><h2>{html.escape(name)}</h2><p class="idea">{html.escape(idea)}</p></div>
    <div class="meta"><span class="family">{family}</span><span class="dist" title="1 = closest to the reference, 5 = clearly distinct">distance <span class="pips">{pips}</span> {dist}/5</span><span class="fit">status fit: {fit}</span></div>
  </header>
  <p class="disttext">{html.escape(dist_text)}</p>
  <div class="grid">
    <div class="block"><h3>Size ladder - 16 · 22 · 32 · 64 · 256, plus the 22 px symbolic</h3>{ladder(num)}</div>
    <div class="block"><h3>Top bar - beside the chip</h3>{topbar(num, "warn", "IO 3 ⚠")}{topbar(num, "ok", "IO ✓")}{topbar(num, "warn", "IO 3 ⚠", "light")}
      <h3>Dock tile</h3><div class="docks">{dock(num, "dark")}{dock(num, "light")}</div></div>
    <div class="block wide"><h3>Face-as-status - OK · warn · error · stale</h3>{status_set(num, gtk)}</div>
  </div>
  <p class="files"><code>brand/logos/{num:02d}-{slug}.svg</code> · <code>brand/logos/{num:02d}-{slug}-symbolic.svg</code> · states in <code>brand/logos/states/</code></p>
</section>"""


CSS = """
:root{--bg:#f7f7f5;--surface:#ffffff;--text:#1f2328;--muted:#5d6670;--faint:#8a929b;--border:#e3e5e8;--accent:#11867a;
--panel-light:#ffffff;--panel-dark:#1d1f22;--code:#f0f1f3}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#141518;--surface:#1c1e22;--text:#e8eaed;
--muted:#a3abb4;--faint:#7c848d;--border:#2e3238;--accent:#5fe0cf;--code:#26292e}}
:root[data-theme="dark"]{--bg:#141518;--surface:#1c1e22;--text:#e8eaed;--muted:#a3abb4;--faint:#7c848d;--border:#2e3238;
--accent:#5fe0cf;--code:#26292e}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Ubuntu,sans-serif}
main{max-width:1180px;margin:0 auto;padding:32px 16px 80px}
h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}
.lede{color:var(--muted);margin:0 0 24px;max-width:860px}
code{background:var(--code);padding:1px 5px;border-radius:4px;font-size:12.5px}
.overview{display:grid;grid-template-columns:repeat(auto-fill,minmax(104px,1fr));gap:8px;margin:12px 0 28px}
.overview a{display:flex;flex-direction:column;align-items:center;gap:6px;padding:10px 4px;background:var(--surface);
border:1px solid var(--border);border-radius:10px;color:var(--text);text-decoration:none;font-size:12px;text-align:center}
.overview .pair{display:flex;align-items:center;gap:8px}
.overview .symb{color:var(--text)}
.top5{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin-bottom:28px}
.top5 h2{margin:0 0 10px;font-size:18px}
.top5 ol{margin:0;padding-left:0;list-style:none;display:grid;gap:10px}
.top5 li{display:grid;grid-template-columns:28px 64px 1fr;gap:14px;align-items:center}
.top5 .rank{font-weight:700;font-size:20px;color:var(--accent)}
.top5 b{display:block}
.top5 span.why{color:var(--muted);font-size:14px}
.rules{color:var(--muted);font-size:13.5px;margin:0 0 28px;padding-left:18px}
.card{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin:0 0 22px}
.card header{display:grid;grid-template-columns:auto 1fr auto;gap:14px;align-items:start}
.num{font:700 26px/1 ui-monospace,"JetBrains Mono",monospace;color:var(--faint);padding-top:2px}
.card h2{margin:0;font-size:20px}
.idea{margin:2px 0 0;color:var(--muted)}
.meta{display:flex;flex-direction:column;align-items:flex-end;gap:4px;font-size:12.5px;color:var(--muted);white-space:nowrap}
.family{border:1px solid var(--border);border-radius:999px;padding:1px 8px}
.pips{display:inline-flex;gap:2px;vertical-align:middle}
.pips i{width:9px;height:9px;border-radius:2px;background:var(--border)}
.pips i.on{background:var(--muted)}
.disttext{margin:10px 0 14px;font-size:13.5px;color:var(--muted)}
.grid{display:grid;grid-template-columns:1.15fr 1fr;gap:16px}
.grid > *,.states > *{min-width:0}
.block.wide{grid-column:1/-1}
.block h3{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);margin:0 0 8px}
.block h3:not(:first-child){margin-top:14px}
.ladder{display:flex;align-items:flex-end;gap:14px;padding:14px;border-radius:10px;flex-wrap:wrap}
.ladder + .ladder{margin-top:8px}
.ladder.light{background:#ffffff;border:1px solid #e3e5e8;color:#2e3436}
.ladder.dark{background:#1d1f22;border:1px solid #2e3238;color:#ffffff}
.ladder figure{margin:0;display:flex;flex-direction:column;align-items:center;gap:4px}
.ladder figcaption{font-size:10.5px;color:#8a929b;font-family:ui-monospace,monospace}
.ladder.st{gap:22px}
.states{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.states .ladder + .ladder{margin-top:0}
.ic{display:block;flex:none}
.topbar{height:32px;display:flex;align-items:center;justify-content:space-between;padding:0 10px;border-radius:8px;
font:600 13px/1 Cantarell,Ubuntu,"Segoe UI",sans-serif;margin-bottom:6px;position:relative}
.topbar.dark{background:#000000;color:#ffffff;--sym-warn:#ffb000;--sym-error:#ff4d4d}
.topbar.light{background:#f6f5f4;color:#2e3436;border:1px solid #dedcda;--sym-warn:#b35c00;--sym-error:#c00000}
.topbar .tb-clock{position:absolute;left:50%;transform:translateX(-50%)}
.topbar .tb-right{display:flex;align-items:center;gap:10px}
.topbar .chip{display:inline-flex;align-items:center;gap:5px;padding:4px 8px;border-radius:999px}
.topbar.dark .chip{background:rgba(255,255,255,.14)}
.topbar.light .chip{background:rgba(0,0,0,.07)}
.topbar.wide .chip{background:transparent}
.tb-sys{display:inline-flex;gap:6px}
.tb-sys i{width:14px;height:14px;border-radius:3px;background:currentColor;opacity:.55}
.docks{display:flex;gap:10px;flex-wrap:wrap}
.dock{display:flex;gap:8px;padding:8px 10px;border-radius:16px}
.dock.dark{background:rgba(20,20,22,.92);border:1px solid #333}
.dock.light{background:rgba(255,255,255,.9);border:1px solid #d8d8d8}
.dock-tile{width:48px;height:48px;border-radius:12px;position:relative;display:flex;align-items:center;justify-content:center}
.dock-tile.other{background:linear-gradient(#8a929b,#5d6670);opacity:.55}
.dock-tile .running{position:absolute;bottom:-6px;left:50%;width:5px;height:5px;margin-left:-2.5px;border-radius:50%;background:#e66100}
.dock.light .dock-tile .running{background:#e66100}
.gtk{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:8px}
.gtk-row{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:8px 12px;border-radius:8px}
.gtk-row.dark{background:#1d1d1d}
.gtk-row.light{background:#fafafa;border:1px solid #e3e5e8}
.gtk-row img{image-rendering:pixelated}
.gtk-row img.zoom{margin-right:8px}
.note{font-size:12px;color:var(--faint);margin:0;flex-basis:100%}
.files{margin:14px 0 0;font-size:12px;color:var(--faint)}
@media (max-width:820px){.grid{grid-template-columns:1fr}.card header{grid-template-columns:auto 1fr}.meta{grid-column:1/-1;align-items:flex-start;flex-direction:row;flex-wrap:wrap}
.states{grid-template-columns:1fr}.top5 li{grid-template-columns:22px 48px 1fr}.ladder figure:last-of-type svg[width="256"]{width:160px;height:160px}}
@media (max-width:520px){.ladder svg[width="256"]{width:128px;height:128px}.topbar .tb-clock,.tb-sys{display:none}
.topbar.wide{height:auto;flex-wrap:wrap;padding:6px 10px}.topbar.wide .tb-right{flex-wrap:wrap;gap:4px}}
"""


def gallery(gtk, problems):
    by_num = {entry[0]: entry for entry in PROPOSALS}
    overview = "".join(f'<a href="#logo-{n:02d}"><span class="pair">{use(f"f{n}-ok", 48)}{use(f"s{n}-ok", 22, "symb")}</span>'
                       f'{n:02d} {html.escape(name)}</a>' for n, _slug, name, *_ in PROPOSALS)
    top = "".join(f'<li><span class="rank">{i}</span>{use(f"f{n}-ok", 64)}<div><b>{n:02d} · {html.escape(name)}</b>'
                  f'<span class="why">{html.escape(why)}</span></div></li>' for i, (n, name, why) in enumerate(TOP5, 1))
    checks = ("all 160 files pass: symbolic files use only rect / circle / path with fills and GTK state classes; "
              "full marks use no text, fonts or raster" if not problems else f"PROBLEMS: {html.escape(str(problems))}")
    cards = "".join(card(by_num[n], gtk) for n in sorted(by_num))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RobotDiagnostics Logos</title><style>{CSS}</style></head>
<body>{sprite()}<main>
<h1>RobotDiagnostics - 20 logo proposals</h1>
<p class="lede">Every mark is an original two-eyes-and-a-line face - the calm, minimal face of a friendly health robot that
Zach likes - and none copies the reference character's head-and-body silhouette or artwork. Each one ships a full-colour
mark (16-256 px, light and dark) and a 22 px monochrome symbolic icon for the GNOME top bar, and each face changes with
health like a pain scale: calm when OK, a sagging line on warn, a grimace on error, closed eyes when the data is stale.
Brand accent is teal; amber and red appear only for problems.</p>
<ul class="rules">
<li>Distance from the reference: <b>1</b> = close in spirit (same calm face on a soft plain shape) to <b>5</b> = clearly distinct (the face is a detail inside something else).</li>
<li>Symbolic icons recolour through GTK's own classes (<code>.warning</code>, <code>.error</code>), so a problem turns the top-bar icon amber or red <i>and</i> changes its shape; stale fades it to 50 %.</li>
<li>File checks: {checks}.</li>
</ul>
<section class="top5"><h2>Top 5</h2><ol>{top}</ol><p class="note" style="margin-top:12px">{html.escape(RUNNER_UP)}</p></section>
<h2 style="font-size:18px;margin:0">All 20</h2>
<div class="overview">{overview}</div>
{cards}
</main></body></html>
"""


def main():
    problems = write_files()
    gtk, sheet = gtk_proof()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(gallery(gtk, problems))
    files = sorted(LOGOS.rglob("*.svg"))
    print(f"wrote {len(files)} svg files under {LOGOS}")
    print(f"checks: {'all pass' if not problems else problems}")
    print(f"gtk sheet: {sheet}")
    print(f"gallery: {REPORT} ({REPORT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
