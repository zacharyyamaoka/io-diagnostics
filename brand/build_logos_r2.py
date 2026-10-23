#!/usr/bin/env python3
"""Build the 10 round-2 RobotDiagnostics logo variants and their gallery (2026-10-23).

Round 2 narrows round 1 to Zach's three picks - 01 Calm Line, 14 Pretty Cushion, 05 Kind Heart - around one idea:
a robot with a heart sticker, where you glance at the heart like a video-game life meter.

Rules this round (Zach, 2026-10-23):
  - the face never changes: the calm two-dot face (two eyes joined by a straight line) in every state;
  - the heart is the signature element;
  - health shows two ways across the set: (a) the heart IS the life meter - full = OK, half = warn, broken = error,
    dotted = stale, each a different SHAPE; (b) the app's own status shapes as a badge - triangle, octagon with a
    cross, hollow clock, disc with a question mark (io_diagnostics/web/tokens.css).

Writes, for every variant r2-NN-<slug>:
  brand/logos/r2-NN-<slug>.svg               full-colour mark, 64 grid
  brand/logos/r2-NN-<slug>-symbolic.svg      22 px monochrome top-bar icon
  brand/logos/states/r2-NN-<slug>-<state>.svg and -<state>-symbolic.svg   warn / error / stale (+ unknown for badges)
and the self-contained gallery reports/media/robotdiagnostics-logos-round2-2026-10-23.html.

Geometry, the symbolic-file checker and the GTK rules are round 1's (build_logos.py, imported, left untouched).
WHY the heart is rose and not coral: coral (#ff7a6b) sits dE2000 3.2 from a dark skin's error red (#ff6a5c), i.e.
the same colour; rose (#f483a5) is at least 14 from every error red in every skin. `colour_study` prints the table.

Run: python3 /home/bam/io-diagnostics/brand/build_logos_r2.py
"""

from __future__ import annotations

import base64
import html
import math
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_logos as r1  # noqa: E402
from build_logos import (EDGE, INK, LOGOS, P, STATES_DIR, SYM_FILL, _unit, arc_pts, circle_d, circle_ring_d,  # noqa: E402
                         cross_verts, densify, face_el, face_knock, fill, fmt, path, rect, rpoly_d, rr_ring_d,
                         signed_area, stroke_d)

REPORT = pathlib.Path("/home/bam/io-diagnostics/reports/media/robotdiagnostics-logos-round2-2026-10-23.html")
SHOTS = pathlib.Path("/home/bam/io-diagnostics/reports/media/round3")
WEB = pathlib.Path("/home/bam/io-diagnostics/io_diagnostics/web")

ROSE = "#f483a5"
CORAL = "#ff7a6b"
APP_RED = "#c83e32"   # tokens.css --io-error, the default skin's octagon
PAL = {"heart": ROSE}  # the colour study swaps this to draw the same marks in coral and in the app's red

# the app's status fills (tokens.css), used for the meter heart and the badges on a light plate
STATUS = {"warn": "#b7791f", "error": APP_RED, "stale": "#8c897f", "unknown": "#7466b8"}
ONDARK = {"ok": "#ffffff", "warn": "#ffb000", "error": "#ff4d4d", "stale": "#7d7d7d"}  # Bare Metal, on black
WHITE = "#ffffff"
STICKER = f' fill="{WHITE}" stroke="{EDGE}" stroke-width="1.2"'

METER_STATES = ("ok", "warn", "error", "stale")
BADGE_STATES = ("ok", "warn", "error", "stale", "unknown")


# ---------------------------------------------------------------- geometry added this round

def poly_d(points):
    return "M" + P(*points[0]) + "".join("L" + P(*p) for p in points[1:]) + "Z"


def rotate(point, centre, degrees):
    """Clockwise on screen for positive degrees (SVG's y points down)."""
    a = math.radians(degrees)
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    return centre[0] + dx * math.cos(a) - dy * math.sin(a), centre[1] + dx * math.sin(a) + dy * math.cos(a)


def offset_pts(points, width):
    """A closed polygon moved inward by width (outward when negative), mitred, as round 1's `inset` does for
    rounded polygons - but for any densely sampled outline, so a 9 px heart can carry a 1 px ring."""
    sign = 1 if signed_area(points) > 0 else -1
    count = len(points)
    out = []
    for i in range(count):
        (x0, y0), (x1, y1), (x2, y2) = points[i - 1], points[i], points[(i + 1) % count]
        ax, ay = _unit(x1 - x0, y1 - y0)
        bx, by = _unit(x2 - x1, y2 - y1)
        n1, n2 = (-ay * sign, ax * sign), (-by * sign, bx * sign)
        sx, sy = _unit(n1[0] + n2[0], n1[1] + n2[1])
        scale = 1 / max(0.35, sx * n1[0] + sy * n1[1])
        out.append((x1 + sx * width * scale, y1 + sy * width * scale))
    return out


def inside(point, polygon):
    x, y = point
    hit = False
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            hit = not hit
    return hit


def rr_pts(x, y, w, h, r, step=0.3):
    """A rounded rectangle's outline as dense points, clockwise from the top-left corner."""
    corners = [(x + r, y + r, 180), (x + w - r, y + r, 270), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90)]
    verts = []
    for cx, cy, a0 in corners:
        verts += arc_pts(cx, cy, r, a0, a0 + 90, max(6, int(math.pi * r / 2 / step)))
    return densify(verts + verts[:1], step)[:-1]


def gapped_ring(centreline, width, blocked):
    """A ring of `width` along a closed centreline, with a round-capped gap wherever blocked(point) - the cut-out that
    lets a sticker or badge break a symbolic outline without overlapping it (overlap would fill solid)."""
    keep = [not blocked(p) for p in centreline]
    if all(keep):
        return poly_d(offset_pts(centreline, -width / 2)) + poly_d(offset_pts(centreline, width / 2)), True
    start = keep.index(False)
    order, flags = centreline[start:] + centreline[:start], keep[start:] + keep[:start]
    runs, run = [], []
    for point, kept in zip(order, flags):
        if kept:
            run.append(point)
        else:
            if len(run) > 1:
                runs.append(run)
            run = []
    if len(run) > 1:
        runs.append(run)
    return "".join(stroke_d(run, width) for run in runs), False


def piece(d_evenodd, attrs=""):
    d, evenodd = d_evenodd
    return path(d, attrs + (' fill-rule="evenodd"' if evenodd else ""))


# ---------------------------------------------------------------- the heart and its life meter

def heart_pts(cx, bottom, hd, tip=0.0, step=0.25, grow=0.0):
    """Round 1's heart (a square on its corner plus two semicircles) as dense points from the bottom point, up the
    left side, over both lobes and down the right; returns (points, index of the top notch). tip > 0 rounds the
    bottom point with that radius (the softer Pretty heart). grow > 0 is the exact outward offset - lobes of radius
    rr + grow meeting at x = cx - because a mitred offset folds into a loop at the concave notch."""
    rr = hd / math.sqrt(2) + grow
    m1, m2 = (cx - hd / 2, bottom - 1.5 * hd), (cx + hd / 2, bottom - 1.5 * hd)
    notch_y = m1[1] - math.sqrt(rr * rr - (hd / 2) ** 2)
    a_left = math.degrees(math.atan2(notch_y - m1[1], cx - m1[0])) % 360
    a_right = math.degrees(math.atan2(notch_y - m2[1], cx - m2[0])) % 360
    count = max(10, int(math.pi * rr / step))
    left = arc_pts(m1[0], m1[1], rr, 135, a_left, count)
    right = arc_pts(m2[0], m2[1], rr, a_right, 405, count)
    if tip:
        centre = (cx, bottom - tip * math.sqrt(2))
        start = arc_pts(centre[0], centre[1], tip + grow, 90, 135, 6)
        end = arc_pts(centre[0], centre[1], tip + grow, 45, 90, 6)[:-1]
        a, b = start[-1], end[0]
    else:
        tip_point = (cx, bottom + grow * math.sqrt(2))
        start, end, a, b = [tip_point], [], tip_point, tip_point
    up = densify([a, left[0]], step)[1:-1]
    down = densify([right[-1], b], step)[1:-1]
    points = start + up + left + right[1:] + down + end
    return points, len(start) + len(up) + len(left) - 1


def heart_centre(cx, bottom, hd):
    return cx, bottom - 1.1 * hd


def heart_shape(cx, bottom, hd, tip=0.0, rot=0.0, grow=0.0):
    """The heart's outline (grown outward by `grow`), rotated about its centre - for stickers and clearances."""
    points, _ = heart_pts(cx, bottom, hd, tip, grow=grow)
    centre = heart_centre(cx, bottom, hd)
    return [rotate(p, centre, rot) for p in points]


def meter_pieces(cx, bottom, hd, state, width, tip=0.0, rot=0.0):
    """The heart as a life meter, every state a different shape:
    ok = full heart; warn = half full (left half solid inside the outline); error = broken in two along a zigzag,
    the halves splayed; stale = the outline as dots (no fresh reading). Returns [(d, evenodd)]."""
    points, notch = heart_pts(cx, bottom, hd, tip)
    centre = heart_centre(cx, bottom, hd)

    def turn(ps):
        return [rotate(p, centre, rot) for p in ps]

    if state == "ok":
        return [(poly_d(turn(points)), False)]
    if state == "warn":
        ring = poly_d(turn(points)) + poly_d(turn(offset_pts(points, width)))
        return [(poly_d(turn(points[:notch + 1])), False), (ring, True)]
    if state == "error":
        gap = max(width * 0.9, hd * 0.17)
        left = [p for p in points[:notch + 1] if p[0] <= cx - gap / 2]
        right = [p for p in points[notch:] + points[:1] if p[0] >= cx + gap / 2]
        top_y, bottom_y = left[-1][1], left[0][1]
        amp = hd * 0.3
        zig = [(cx + o, top_y + (bottom_y - top_y) * t)
               for t, o in zip((0, 0.28, 0.52, 0.76, 1), (0, -amp, amp, -amp * 0.6, 0))]
        left_piece = left + [(x - gap / 2, y) for x, y in zig]
        right_piece = right + [(x + gap / 2, y) for x, y in reversed(zig)]
        pivot = (cx, bottom)
        left_piece = [rotate(p, pivot, -9) for p in left_piece]
        right_piece = [rotate(p, pivot, 9) for p in right_piece]
        return [(poly_d(turn(left_piece)), False), (poly_d(turn(right_piece)), False)]
    if state == "stale":
        centreline = offset_pts(points, width / 2)
        lengths = [0.0]
        for a, b in zip(centreline, centreline[1:] + centreline[:1]):
            lengths.append(lengths[-1] + math.dist(a, b))
        total = lengths[-1]
        # WHY a 0.85 px floor on the dot and a 3 px floor on the pitch: below that a 22 px icon's dots blur into grey
        dot = max(width * 0.62, 1.0)
        count = max(4, 2 * round(total / max(width * 3.1, 3.6) / 2))
        dots, j = [], 0
        for k in range(count):
            target = total * k / count
            while lengths[j + 1] < target:
                j += 1
            a, b = centreline[j], centreline[(j + 1) % len(centreline)]
            t = (target - lengths[j]) / max(1e-9, lengths[j + 1] - lengths[j])
            dots.append(rotate((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), centre, rot))
        return [("".join(circle_d(x, y, dot) for x, y in dots), False)]
    raise ValueError(state)


def ind(state, sym, colours=None):
    """The indicator's paint: a GTK state class in a symbolic file (so only the heart or badge turns amber/red in the
    top bar while the face keeps the theme's ink), the state's fill in a full-colour mark."""
    if sym:
        return {"warn": ' class="warning"', "error": ' class="error"'}.get(state, "")
    colours = colours or {"ok": PAL["heart"], **STATUS}
    return fill(colours[state])


def meter(cx, bottom, hd, state, width, sym, tip=0.0, rot=0.0, colours=None):
    return "".join(piece(p, ind(state, sym, colours)) for p in meter_pieces(cx, bottom, hd, state, width, tip, rot))


def sticker(cx, bottom, hd, grow, tip=0.0, rot=0.0):
    return path(poly_d(heart_shape(cx, bottom, hd, tip, rot, grow)), STICKER)


# ---------------------------------------------------------------- the app's status shapes, as a badge

def x_cross(cx, cy, arm, length):
    return [rotate((cx + x, cy + y), (cx, cy), 45) for x, y in cross_verts(0, arm, length)]


def badge_pieces(bx, by, radius, state):
    """The badge for state at centre (bx, by): a heart when OK, else the app's own status shape with its glyph
    knocked out (tokens.css: triangle / octagon with a cross / hollow clock / disc with a question mark)."""
    R = radius
    if state == "ok":
        hd = 2 * R * 0.95 / 2.414
        return meter_pieces(bx, by + 1.1035 * hd, hd, "ok", 1)
    if state == "warn":
        tri = [(bx, by - R * 1.0), (bx + R * 1.08, by + R * 0.86), (bx - R * 1.08, by + R * 0.86)]
        bar_w = R * 0.26
        d = (rpoly_d(tri, [R * 0.24] * 3) + r1.rr_d(bx - bar_w / 2, by - R * 0.4, bar_w, R * 0.66, bar_w / 2)
             + circle_d(bx, by + R * 0.53, R * 0.15))
        return [(d, True)]
    if state == "error":
        octagon = [(bx + R * math.cos(math.radians(22.5 + 45 * k)), by + R * math.sin(math.radians(22.5 + 45 * k)))
                   for k in range(8)]
        cross = x_cross(bx, by, R * 0.13, R * 0.55)
        return [(rpoly_d(octagon, [R * 0.12] * 8) + poly_d(list(reversed(cross))), True)]
    if state == "stale":
        hands = stroke_d([(bx, by - R * 0.52), (bx, by), (bx + R * 0.4, by + R * 0.04)], R * 0.22)
        return [(circle_ring_d(bx, by, R, R * 0.22), True), (hands, False)]
    if state == "unknown":
        hook = arc_pts(bx, by - R * 0.24, R * 0.3, 195, 395, 14) + [(bx + R * 0.02, by + R * 0.1), (bx, by + R * 0.24)]
        d = circle_d(bx, by, R) + stroke_d(hook, R * 0.21) + circle_d(bx, by + R * 0.53, R * 0.135)
        return [(d, True)]
    raise ValueError(state)


def badge(bx, by, radius, state, sym, backing=0.0):
    """Full-colour badges sit on a white round sticker (backing = its extra radius); symbolic ones cut a gap instead."""
    out = "" if sym or not backing else r1.circle(bx, by, radius + backing, STICKER)
    colours = {"ok": PAL["heart"], **STATUS}
    return out + "".join(piece(p, ind(state, sym, colours)) for p in badge_pieces(bx, by, radius, state))


def gap_around(circle_or_zone, clearance=0.0):
    """blocked(point) for gapped_ring: within clearance of a (cx, cy, radius) badge, or inside an already-grown shape."""
    if isinstance(circle_or_zone, tuple):
        cx, cy, radius = circle_or_zone
        return lambda p: math.dist(p, (cx, cy)) < radius + clearance
    return lambda p: inside(p, circle_or_zone)


def head(x, y, w, h, r, attrs=f' fill="{WHITE}" stroke="{EDGE}" stroke-width="2"'):
    return rect(x, y, w, h, r, attrs)


# ---------------------------------------------------------------- the 10 variants
# Each builder returns the inner SVG for the 64 grid (sym=False) or the 22 grid (sym=True). The face is always the
# same flat two-dot face: face_el(..., "flat").

def v01(st, sym):
    """Cheek Sticker: Calm Line's cushion with a heart sticker on the lower-right cheek, breaking the edge."""
    if sym:
        hx, hb, hd, rot = 16.3, 21.5, 3.75, -10
        blocked = gap_around(heart_shape(hx, hb, hd, rot=rot, grow=1.0 + 0.9))
        ring = piece(gapped_ring(rr_pts(1.9, 3.9, 18.2, 14.2, 5.1), 1.8, blocked))
        return ring + face_el(10.4, 9.8, 4.6, 2.1, 1.5, "flat") + meter(hx, hb, hd, st, 1.1, True, rot=rot)
    hx, hb, hd, rot = 47, 57.5, 7.4, -10
    return (head(5, 9, 54, 44, 17) + face_el(31, 29, 13, 5.6, 3.4, "flat", fill(INK))
            + sticker(hx, hb, hd, 2.6, rot=rot) + meter(hx, hb, hd, st, 2.3, False, rot=rot))


def v02(st, sym):
    """Crown Heart: the heart sits on top of the head, like the life heart floating over a game character."""
    if sym:
        hx, hb, hd = 11, 10.3, 3.9
        blocked = gap_around(heart_shape(hx, hb, hd, grow=1.0 + 0.85))
        ring = piece(gapped_ring(rr_pts(1.85, 7.35, 18.3, 12.8, 4.65), 1.7, blocked))
        return ring + face_el(11, 14.3, 4.6, 2.0, 1.45, "flat") + meter(hx, hb, hd, st, 1.1, True)
    hx, hb, hd = 32, 28.5, 8.4
    return (head(5, 19, 54, 40, 17) + face_el(32, 40, 13, 5.6, 3.4, "flat", fill(INK))
            + sticker(hx, hb, hd, 2.6) + meter(hx, hb, hd, st, 2.5, False))


def v03(st, sym):
    """Chest Heart: head and shoulders with the heart on the chest (the second reference's layout), but a square-ish
    cushion head on a flat-based bust - no oval head, no inflated body."""
    if sym:
        hx, hb, hd = 11, 21.3, 3.3
        head_ring = path(rr_ring_d(4, 0.6, 14, 9.8, 4.3, 1.45), ' fill-rule="evenodd"')
        arch = piece(gapped_ring(rr_pts(1.75, 12.2, 18.5, 13, 6), 1.5, lambda p: p[1] > 21.3))
        return (head_ring + face_el(11, 5.5, 3.5, 1.55, 1.15, "flat") + arch + meter(hx, hb, hd, st, 1.05, True))
    hx, hb, hd = 32, 55, 6.6
    body = path(rpoly_d(r1.box(5, 33, 54, 26), [19, 19, 6, 6]), f' fill="{WHITE}" stroke="{EDGE}" stroke-width="2"')
    return (body + head(15, 4, 34, 25, 12.5) + face_el(32, 16.5, 9.5, 4.2, 2.6, "flat", fill(INK))
            + sticker(hx, hb, hd, 2.2) + meter(hx, hb, hd, st, 2.1, False))


TEAL_TILE = ("#1fae9c", "#0c6f64")


def v04(st, sym):
    """Heart Cushion: Pretty Cushion's teal tile and white cushion face, with the life heart below. The tile stays
    teal in every state - only the heart moves."""
    if sym:
        return (path(rr_ring_d(1, 1, 20, 20, 5.5, 1.6), ' fill-rule="evenodd"') + face_el(11, 7.3, 4.5, 1.9, 1.35, "flat")
                + meter(11, 18.4, 3.35, st, 1.05, True))
    top, bottom = TEAL_TILE
    uid = f"r2t4{st}{PAL['heart'][1:]}"
    return (f'<defs><linearGradient id="{uid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient>'
            f'<linearGradient id="{uid}c" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/>'
            f'<stop offset="1" stop-color="#e6f0ee"/></linearGradient></defs>'
            + rect(4, 4, 56, 56, 15, f' fill="url(#{uid})"')
            + rect(10, 10.5, 44, 26, 12, ' fill="#05342f" opacity="0.22"')
            + rect(10, 8, 44, 26, 12, f' fill="url(#{uid}c)"')
            + face_el(32, 21, 11, 4.6, 2.8, "flat", fill(INK))
            + sticker(32, 55.5, 7.1, 2.2, tip=1.2) + meter(32, 55.5, 7.1, st, 2.2, False, tip=1.2))


def v05(st, sym):
    """Heart Badge: Calm Line's cushion with a corner badge - the heart when OK, the app's status shape otherwise."""
    if sym:
        bx, by, radius = 17.2, 4.8, 4.5
        ring = piece(gapped_ring(rr_pts(1.85, 6.35, 15.1, 13.8, 4.15), 1.7, gap_around((bx, by, radius), 1.05 + 0.85)))
        return ring + face_el(9.4, 13.25, 3.9, 1.8, 1.3, "flat") + badge(bx, by, radius, st, True)
    return (head(4, 15, 48, 42, 16) + face_el(28, 36, 11.5, 5, 3, "flat", fill(INK))
            + badge(48.5, 15.5, 11.2, st, False, backing=2.6))


def v06(st, sym):
    """Heart Head: the head is the heart (Kind Heart), the calm face on it, the status badge at the lower right."""
    if sym:
        hx, hb, hd = 9.6, 17.8, 6.4
        points, _ = heart_pts(hx, hb, hd)
        face = face_knock(hx, hb - 1.276 * hd, 3.12, 1.38, 1.0, "flat")
        out = path(poly_d(points) + face, ' fill-rule="evenodd"')
        return out + ("" if st == "ok" else badge(17.7, 17.7, 4.25, st, True))
    hx, hb, hd = 29, 53.5, 19.5
    points, _ = heart_pts(hx, hb, hd, tip=2.5)
    out = path(poly_d(points), fill(PAL["heart"])) + face_el(hx, hb - 1.2 * hd, 11, 4.8, 3, "flat", fill(INK))
    return out + ("" if st == "ok" else badge(51.5, 51.5, 9.8, st, False, backing=2.4))


def v07(st, sym):
    """Bare Heart: Bare Metal - the face glyph and the heart glyph side by side on black, one line weight, no hue
    when OK (the heart is white)."""
    if sym:
        return face_el(5.2, 11, 3.4, 1.7, 1.3, "flat") + meter(16.3, 11 + 3.97, 3.6, st, 1.15, True)
    return (rect(4, 4, 56, 56, 9, ' fill="#000000" stroke="#4a4a4a" stroke-width="1.5"')
            + face_el(19.5, 32, 7.5, 3.6, 2.4, "flat", fill(WHITE)) + meter(45, 32 + 6.85, 6.2, st, 2.4, False, colours=ONDARK))


PINK_TILE = ("#fde0e7", "#f5b3c4")


def v08(st, sym):
    """Pretty Pink: a soft pink app tile (the second reference's palette) with a white head and shoulders and a chest
    heart; the status badge sits on the tile's corner like an app badge. Symbolic: the bust, the badge top right."""
    if sym:
        bx, by, radius = 18.2, 4.0, 3.7
        blocked = (lambda p: False) if st == "ok" else gap_around((bx, by, radius), 1.0 + 0.72)
        head_ring = piece(gapped_ring(rr_pts(2.22, 1.32, 12.56, 8.36, 3.6), 1.45, blocked))
        arch = piece(gapped_ring(rr_pts(1.75, 12.2, 18.5, 13, 6), 1.5, lambda p: p[1] > 21.3))
        return (head_ring + face_el(8.5, 5.5, 3.5, 1.55, 1.15, "flat") + arch + meter(11, 20.6, 2.45, "ok", 1, True)
                + ("" if st == "ok" else badge(bx, by, radius, st, True)))
    top, bottom = PINK_TILE
    uid = f"r2p8{st}{PAL['heart'][1:]}"
    return (f'<defs><linearGradient id="{uid}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient>'
            f'<clipPath id="{uid}k">{rect(4, 4, 56, 56, 15)}</clipPath></defs>'
            + rect(4, 4, 56, 56, 15, f' fill="url(#{uid})"')
            + f'<g clip-path="url(#{uid}k)">'
            + path(rpoly_d(r1.box(9, 42, 46, 30), [17, 17, 0, 0]), f' fill="{WHITE}"')
            + "</g>"
            + rect(14, 12.5, 36, 26, 12.5, ' fill="#b05570" opacity="0.18"')
            + rect(14, 10, 36, 26, 12.5, f' fill="{WHITE}"')
            + face_el(32, 23, 9.5, 4.2, 2.6, "flat", fill(INK))
            + meter(32, 57, 4.8, "ok", 1, False, tip=0.8)
            + ("" if st == "ok" else badge(51, 13, 8.6, st, False, backing=2.4)))


LIVES = {"ok": ("ok", "ok", "ok"), "warn": ("ok", "ok", "empty"), "error": ("ok", "empty", "empty"),
         "stale": ("stale", "stale", "stale")}


def life_heart(cx, bottom, hd, kind, width, state, sym):
    """One life: a full heart, an empty outline, or the stale dotted outline - painted in the row's state colour."""
    if kind == "empty":
        points, _ = heart_pts(cx, bottom, hd)
        return piece((poly_d(points) + poly_d(offset_pts(points, width)), True), ind(state, sym))
    return "".join(piece(p, ind(state, sym)) for p in meter_pieces(cx, bottom, hd, kind, width))


def v09(st, sym):
    """Life Row: three hearts under the face, counted like a game's lives - 3 OK, 2 warn, 1 error, dotted when stale."""
    if sym:
        out = path(rr_ring_d(1, 1, 20, 12, 4.5, 1.5), ' fill-rule="evenodd"') + face_el(11, 7, 5, 1.75, 1.25, "flat")
        return out + "".join(life_heart(cx, 21.3, 2.6, kind, 0.95, st, True) for cx, kind in zip((3.6, 11, 18.4), LIVES[st]))
    out = head(6, 5, 52, 34, 15) + face_el(32, 22, 13, 5.4, 3.2, "flat", fill(INK))
    return out + "".join(life_heart(cx, 57, 5.4, kind, 2.0, st, False) for cx, kind in zip((14.5, 32, 49.5), LIVES[st]))


def v10(st, sym):
    """Heart Body: the head sits on a heart-shaped body, and the body is the life meter."""
    if sym:
        return (path(rr_ring_d(3.5, 0.6, 15, 9.8, 4.3, 1.4), ' fill-rule="evenodd"') + face_el(11, 5.5, 3.9, 1.6, 1.15, "flat")
                + meter(11, 21.6, 4.3, st, 1.15, True))
    return (meter(32, 61, 12.6, st, 3.0, False, tip=2.5)
            + head(13, 3, 38, 25, 12.5) + face_el(32, 15.5, 9.5, 4.2, 2.6, "flat", fill(INK)))


# num, slug, name, idea, health (a = heart meter, b = status badge), distance (1 = closest to the references,
# 5 = clearly distinct), distance text, round-1 parent, builder
VARIANTS = [
    (1, "cheek-sticker", "Cheek Sticker", "Calm Line's cushion with a heart sticker on the cheek, breaking the "
     "lower-right edge. The heart is the life meter.", "a", 2,
     "Close in spirit to both references (calm face, small heart sticker), but a rounded-square cushion with no "
     "body, arms or oval head; the sticker sits on the cheek, not the chest.", "01 Calm Line", v01),
    (2, "crown-heart", "Crown Heart", "The life heart sits on top of the head, like the heart floating over a "
     "game character.", "a", 3, "The game read comes first; the references never put the heart above the head.",
     "01 Calm Line", v02),
    (3, "chest-heart", "Chest Heart", "Head and shoulders with the heart on the chest - the second reference's "
     "layout, redrawn.", "a", 1, "Closest. Same idea as the second reference (head over shoulders, chest heart), "
     "so it borrows the most; kept original with a rounded-square head, a flat-based bust and a centred heart - "
     "no oval head, no inflated pear body.", "01 Calm Line + 05 Kind Heart", v03),
    (4, "heart-cushion", "Heart Cushion", "Pretty Cushion's teal tile and white face, with the life heart below. "
     "The tile stays teal; only the heart moves.", "a", 3, "An app tile first; face and heart are generic parts "
     "laid on it.", "14 Pretty Cushion", v04),
    (5, "heart-badge", "Heart Badge", "A corner badge: the heart when OK, the app's own triangle, octagon, clock "
     "or ? when not.", "b", 3, "A notification-badge idea; the robot is only the calm cushion face.", "01 Calm Line",
     v05),
    (6, "heart-head", "Heart Head", "The head is the heart; the calm face sits on it and the status badge appears "
     "at the lower right.", "b", 4, "Distinct: a heart with a face, no robot silhouette at all.", "05 Kind Heart",
     v06),
    (7, "bare-heart", "Bare Heart", "Bare Metal: the face glyph and the heart glyph side by side on black, one line "
     "weight; the heart is white when OK.", "a", 5, "Clearly distinct: two terminal glyphs, nothing soft.",
     "11 Bracket Face / Bare Metal skin", v07),
    (8, "pretty-pink", "Pretty Pink", "A soft pink tile, white head and shoulders, chest heart; the status badge "
     "sits on the tile corner like an app badge.", "b", 2, "Close in palette and layout to the second reference "
     "(pink ground, white figure, chest heart); a square tile, a cushion head and a cropped bust keep it original.",
     "14 Pretty Cushion", v08),
    (9, "life-row", "Life Row", "Three hearts under the face, counted like a game's lives: 3 OK, 2 warn, 1 error, "
     "dotted when stale.", "a", 4, "Distinct: a game HUD with a face, not a character.", "own", v09),
    (10, "heart-body", "Heart Body", "The head sits on a heart-shaped body, and the body is the life meter.", "a", 3,
     "A chibi of our own: cushion head on a heart. Shares the 'heart on the body' idea with the references, not "
     "their shapes.", "own", v10),
]

TOP3 = [
    (1, "Cheek Sticker", "Most like the picture Zach described: the Calm Line face he picked, wearing a heart sticker. "
        "The heart stays a readable 9 px in the 22 px top-bar icon because it breaks the head's edge instead of "
        "squeezing inside it, so full, half, broken and dotted all read at a glance."),
    (5, "Heart Badge", "Speaks the app's own language: OK is the heart, every problem is the exact shape the app "
        "already uses (triangle, octagon, clock, ?). Of the three badge variants it is the only one where the heart "
        "is always there, and the badge never collides with the face."),
    (10, "Heart Body", "The strongest life meter: the heart is the biggest shape in the mark, so the state reads "
        "from across the room in the dock and at 22 px. Our own character, taken from neither reference."),
]
RUNNER_UP = ("Runner-up: 04 Heart Cushion - the best dock tile (Pretty Cushion's look with a heart), but its "
             "symbolic heart is the smallest of the meter variants.")


def states_of(entry):
    return BADGE_STATES if entry[4] == "b" else METER_STATES


# ---------------------------------------------------------------- the colour question

def _lab(hexs):
    r, g, b = (int(hexs[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    x = (0.4124 * lin[0] + 0.3576 * lin[1] + 0.1805 * lin[2]) / 0.95047
    y = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    z = (0.0193 * lin[0] + 0.1192 * lin[1] + 0.9505 * lin[2]) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116  # noqa: E731
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def de2000(c1, c2):
    """CIEDE2000 colour difference (about 2 = just noticeable side by side; under 5 = the same colour at a glance)."""
    L1, a1, b1 = _lab(c1)
    L2, a2, b2 = _lab(c2)
    cbar = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(cbar ** 7 / (cbar ** 7 + 25 ** 7)))
    a1p, a2p = a1 * (1 + g), a2 * (1 + g)
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1p, h2p = math.degrees(math.atan2(b1, a1p)) % 360, math.degrees(math.atan2(b2, a2p)) % 360
    dh = 0.0 if c1p * c2p == 0 else (h2p - h1p + 180) % 360 - 180
    dHp = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    lbar, cbarp = (L1 + L2) / 2, (c1p + c2p) / 2
    if c1p * c2p == 0:
        hbar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbar = (h1p + h2p) / 2
    else:
        hbar = (h1p + h2p + 360) / 2 if h1p + h2p < 360 else (h1p + h2p - 360) / 2
    t = (1 - 0.17 * math.cos(math.radians(hbar - 30)) + 0.24 * math.cos(math.radians(2 * hbar))
         + 0.32 * math.cos(math.radians(3 * hbar + 6)) - 0.20 * math.cos(math.radians(4 * hbar - 63)))
    rt = -math.sin(math.radians(60 * math.exp(-(((hbar - 275) / 25) ** 2)))) * 2 * math.sqrt(cbarp ** 7 / (cbarp ** 7 + 25 ** 7))
    sl = 1 + 0.015 * (lbar - 50) ** 2 / math.sqrt(20 + (lbar - 50) ** 2)
    sc, sh = 1 + 0.045 * cbarp, 1 + 0.015 * cbarp * t
    dl, dc = (L2 - L1) / sl, (c2p - c1p) / sc
    return math.sqrt(dl ** 2 + dc ** 2 + (dHp / sh) ** 2 + rt * dc * (dHp / sh))


def app_reds():
    """Every --io-error any skin sets (light and dark), read from the app's CSS/JS."""
    found = set()
    for file in WEB.rglob("*"):
        if file.suffix in (".css", ".js"):
            found.update(c.lower() for c in re.findall(r"--io-error:\s*(#[0-9a-fA-F]{6})", file.read_text(errors="ignore")))
    return sorted(found)


CANDIDATES = [("rose", ROSE, "Rose - recommended"), ("coral", CORAL, "Coral"), ("red", APP_RED, "The app's red")]


def colour_study():
    reds = app_reds()
    rows = []
    for key, colour, label in CANDIDATES:
        nearest = min((de2000(colour, red), red) for red in reds)
        rows.append((key, colour, label, nearest, de2000(colour, APP_RED)))
    return reds, rows


# ---------------------------------------------------------------- files

def write_files():
    LOGOS.mkdir(parents=True, exist_ok=True)
    STATES_DIR.mkdir(parents=True, exist_ok=True)
    problems, count = {}, 0
    for entry in VARIANTS:
        num, slug, name, *_rest, build = entry
        stem = f"r2-{num:02d}-{slug}"
        for state in states_of(entry):
            suffix = "" if state == "ok" else f" ({state})"
            full = r1.svg_file(build(state, False), 64, f"RobotDiagnostics - {name}{suffix}")
            sym = r1.svg_file(build(state, True), 22, f"RobotDiagnostics - {name} symbolic{suffix}", symbolic=True)
            full_path = LOGOS / f"{stem}.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}.svg"
            sym_path = LOGOS / f"{stem}-symbolic.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}-symbolic.svg"
            full_path.write_text(full)
            sym_path.write_text(sym)
            count += 2
            found = r1.check_full(full) + r1.check_symbolic(sym)
            if found:
                problems[f"{stem}/{state}"] = found
    return problems, count


def sym_file(entry, state):
    stem = f"r2-{entry[0]:02d}-{entry[1]}"
    return LOGOS / f"{stem}-symbolic.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}-symbolic.svg"


def gtk_proof():
    """Each symbolic file through Gtk.IconInfo.load_symbolic at 22 px, as round 1 did."""
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
    sheet = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, cell * 10 + 12, cell * len(VARIANTS) + 12)
    sheet.fill(0x808080ff)
    for row, entry in enumerate(VARIANTS):
        for col, (theme_name, (fg, warn, err, bg)) in enumerate(themes.items()):
            for k, state in enumerate(states_of(entry)):
                info = theme.lookup_by_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(sym_file(entry, state)))), 22,
                                             Gtk.IconLookupFlags.FORCE_SIZE)
                pix, was_symbolic = info.load_symbolic(rgba(fg), rgba(fg), rgba(warn), rgba(err))
                assert was_symbolic, sym_file(entry, state)
                assert (pix.get_width(), pix.get_height()) == (22, 22), sym_file(entry, state)
                ok, data = pix.save_to_bufferv("png", [], [])
                out[(entry[0], state, theme_name)] = base64.b64encode(data).decode()
                tile = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 22 * scale, 22 * scale)
                tile.fill(int(bg[1:], 16) << 8 | 0xff)
                big = pix.scale_simple(22 * scale, 22 * scale, GdkPixbuf.InterpType.NEAREST)
                big.composite(tile, 0, 0, 22 * scale, 22 * scale, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
                tile.copy_area(0, 0, 22 * scale, 22 * scale, sheet, 12 + (col * 5 + k) * cell, 12 + row * cell)
    SHOTS.mkdir(parents=True, exist_ok=True)
    sheet_path = SHOTS / "logos-r2-gtk-symbolic-sheet.png"
    sheet.savev(str(sheet_path), "png", [], [])
    return out, sheet_path


# ---------------------------------------------------------------- gallery

def sym_symbol(content):
    content = content.replace('class="warning"', 'class="warning" style="fill:var(--sym-warn,currentColor)"')
    return content.replace('class="error"', 'class="error" style="fill:var(--sym-error,currentColor)"')


def sprite():
    symbols = []
    for entry in VARIANTS:
        num, *_rest, build = entry
        for state in states_of(entry):
            symbols.append(f'<symbol id="f{num}-{state}" viewBox="0 0 64 64">{build(state, False)}</symbol>')
            symbols.append(f'<symbol id="s{num}-{state}" viewBox="0 0 22 22"><g fill="currentColor">'
                           f'{sym_symbol(build(state, True))}</g></symbol>')
    for key, colour, _label in CANDIDATES:  # the colour study: the same marks in each candidate heart colour
        PAL["heart"] = colour
        for num, build in ((1, v01), (10, v10), (6, v06)):
            for state in ("ok", "error"):
                symbols.append(f'<symbol id="c{key}{num}-{state}" viewBox="0 0 64 64">{build(state, False)}</symbol>')
    PAL["heart"] = ROSE
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true">'
            + "".join(symbols) + "</svg>")


use = r1.use
LABELS = {"ok": "IO ✓", "warn": "IO 3 ⚠", "error": "IO 1 ✖", "stale": "IO ◷", "unknown": "IO ?"}


def status_set(entry, gtk):
    num = entry[0]
    states = states_of(entry)
    full_l = "".join(f'<figure>{use(f"f{num}-{s}", 64)}<figcaption>{s}</figcaption></figure>' for s in states)
    bar = "".join(f'<span class="chip">{use(f"s{num}-{s}", 22, "tb-ic")}<span>{LABELS[s]}</span></span>' for s in states)

    def proof(theme):
        return "".join(f'<img src="data:image/png;base64,{gtk[(num, s, theme)]}" width="22" height="22" alt="{s}">'
                       f'<img class="zoom" src="data:image/png;base64,{gtk[(num, s, theme)]}" width="66" height="66" alt="">'
                       for s in states)
    return (f'<div class="states"><div class="ladder light st">{full_l}</div><div class="ladder dark st">{full_l}</div></div>'
            f'<div class="topbar dark wide"><span class="tb-left">{" · ".join(states)}</span><span class="tb-right">{bar}</span></div>'
            f'<div class="gtk"><div class="gtk-row dark">{proof("dark")}</div><div class="gtk-row light">{proof("light")}</div>'
            '<p class="note">Real GTK 3.24 <code>Gtk.IconInfo.load_symbolic</code> output of the shipped <code>-symbolic.svg</code> '
            'files at 22 px (left of each pair) and the same pixels at 3x, nearest-neighbour (right).</p></div>')


HEALTH = {"a": "(a) heart = life meter", "b": "(b) status badge"}


def card(entry, gtk):
    num, slug, name, idea, health, dist, dist_text, parent, _build = entry
    pips = "".join(f'<i class="{"on" if i < dist else ""}"></i>' for i in range(5))
    files = (f"<code>brand/logos/r2-{num:02d}-{slug}.svg</code> · <code>brand/logos/r2-{num:02d}-{slug}-symbolic.svg</code> · "
             f"states <code>brand/logos/states/r2-{num:02d}-{slug}-{{{','.join(states_of(entry)[1:])}}}[-symbolic].svg</code>")
    return f"""
<section class="card" id="logo-{num:02d}">
  <header>
    <div class="num">{num:02d}</div>
    <div class="title"><h2>{html.escape(name)}</h2><p class="idea">{html.escape(idea)}</p></div>
    <div class="meta"><span class="family">{HEALTH[health]}</span><span class="dist" title="1 = closest to the references, 5 = clearly distinct">distance <span class="pips">{pips}</span> {dist}/5</span><span class="fit">from {html.escape(parent)}</span></div>
  </header>
  <p class="disttext">{html.escape(dist_text)}</p>
  <div class="grid">
    <div class="block"><h3>Size ladder - 16 · 22 · 32 · 64 · 256, plus the 22 px symbolic</h3>{r1.ladder(num)}</div>
    <div class="block"><h3>Top bar - beside the chip</h3>{r1.topbar(num, "warn", "IO 3 ⚠")}{r1.topbar(num, "ok", "IO ✓")}{r1.topbar(num, "warn", "IO 3 ⚠", "light")}
      <h3>Dock tile</h3><div class="docks">{r1.dock(num, "dark")}{r1.dock(num, "light")}</div></div>
    <div class="block wide"><h3>Health states - the face never changes</h3>{status_set(entry, gtk)}</div>
  </div>
  <p class="files">{files}</p>
</section>"""


def colour_section(reds, rows):
    swatches = "".join(f'<span class="sw" style="background:{red}" title="{red}"></span>' for red in reds)
    out = []
    for key, colour, label, (nearest, red), vs_default in rows:
        marks = "".join(f'<figure>{use(f"c{key}{n}-{s}", size)}<figcaption>{n:02d} {s}</figcaption></figure>'
                        for n, size in ((1, 64), (10, 64), (6, 64)) for s in ("ok", "error"))
        verdict = ("the same colour as an error red at a glance" if nearest < 5 else
                   "close to an error red" if nearest < 10 else "clearly not an error red")
        out.append(f'<div class="cand"><div class="cand-head"><span class="sw big" style="background:{colour}"></span>'
                   f'<div><b>{html.escape(label)}</b> <code>{colour}</code><br><span class="why">nearest app error red '
                   f'<code>{red}</code> <span class="sw" style="background:{red}"></span> at dE2000 <b>{nearest:.1f}</b> - '
                   f'{verdict}; vs the default skin\'s <code>{APP_RED}</code>: {vs_default:.1f}</span></div></div>'
                   f'<div class="ladder light st">{marks}</div><div class="ladder dark st">{marks}</div></div>')
    return f"""
<section class="colour" id="colour"><h2>Heart colour - rose, coral, or the app's red</h2>
<p class="why"><b>Recommendation: rose <code>{ROSE}</code></b> for the OK heart, with the app's own amber and red only on the
warn and error hearts. Measured against every error red the app's skins set ({len(reds)} of them, light and dark:
{swatches}), coral is dE2000 {rows[1][3][0]:.1f} from <code>{rows[1][3][1]}</code> - a dark skin's error red, the same colour
at a glance - so a coral OK heart would sit next to coral-red error badges in the app. Rose is at least
{rows[0][3][0]:.1f} from every one: it still reads as "heart", never as "error". The app's red as the OK heart makes OK and
error the same colour and leaves only the shape to tell them apart.</p>
<p class="why"><b>The trade-off.</b> The top-bar icon is symbolic, so it is never red when OK whatever the brand colour:
GTK paints it with the panel's ink and colours only the <code>.warning</code> / <code>.error</code> parts, which here are only the
heart or the badge. A red brand heart is therefore safe in the top bar; the risk is the full-colour mark - the dock,
the window icon, the app header - where a red heart sits beside red error octagons all day and trains the eye to ignore
red. Red is also the classic game life heart, so it is the most game-like option. Rose keeps the warmth of the first
reference's heart and the second reference's pink, and gives up a little of the "game heart" punch.</p>
{"".join(out)}
</section>"""


EXTRA_CSS = """
.colour{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:18px 20px;margin-bottom:28px}
.colour h2{margin:0 0 10px;font-size:18px}
.colour .why{color:var(--muted);font-size:14px;margin:0 0 10px}
.cand{margin-top:14px}
.cand-head{display:flex;gap:12px;align-items:center;margin-bottom:8px}
.sw{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:middle;border:1px solid rgba(0,0,0,.15);margin:0 1px}
.sw.big{width:34px;height:34px;border-radius:8px;flex:none}
.cand .ladder{gap:18px}
.cand .ladder + .ladder{margin-top:6px}
"""


def gallery(gtk, problems, count, reds, rows):
    by_num = {entry[0]: entry for entry in VARIANTS}
    overview = "".join(f'<a href="#logo-{n:02d}"><span class="pair">{use(f"f{n}-ok", 48)}{use(f"s{n}-ok", 22, "symb")}</span>'
                       f'{n:02d} {html.escape(name)}</a>' for n, _slug, name, *_ in VARIANTS)
    top = "".join(f'<li><span class="rank">{i}</span>{use(f"f{n}-ok", 64)}<div><b>{n:02d} · {html.escape(name)}</b>'
                  f'<span class="why">{html.escape(why)}</span></div></li>' for i, (n, name, why) in enumerate(TOP3, 1))
    checks = (f"all {count} files pass: symbolic files use only rect / circle / path with fills and GTK state classes; "
              "full marks use no text, fonts or raster" if not problems else f"PROBLEMS: {html.escape(str(problems))}")
    cards = "".join(card(by_num[n], gtk) for n in sorted(by_num))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RobotDiagnostics Logos Round 2</title><style>{r1.CSS}{EXTRA_CSS}</style></head>
<body>{sprite()}<main>
<h1>RobotDiagnostics - round 2: the heart</h1>
<p class="lede">Ten variants on 01 Calm Line, 14 Pretty Cushion and 05 Kind Heart, built around one picture: a calm robot
wearing a heart sticker that you glance at like a video-game life meter. The face is the same in every mark and every
state - two eyes joined by a straight line, never sad or shut. Health shows in the heart instead, two ways:
<b>(a)</b> the heart is the meter - full when OK, half when warn, broken when error, dotted when stale, each a different
shape; or <b>(b)</b> a badge with the app's own status shapes - the heart when OK, then the triangle, the octagon with a
cross, the hollow clock and the disc with a question mark.</p>
<ul class="rules">
<li>Distance from the references: <b>1</b> = close in spirit (calm face plus a heart on a white figure) to <b>5</b> = clearly
distinct. All ten are original geometry - circles, rounded rectangles and the round-1 heart - with no Disney silhouette,
oval-head-on-pear-body outline or inflated-vinyl shading.</li>
<li>In the top bar only the heart or the badge takes colour (GTK's <code>.warning</code> / <code>.error</code> classes); the head
and face keep the panel's ink, and stale changes shape only.</li>
<li>File checks: {checks}.</li>
</ul>
<section class="top5"><h2>Top 3</h2><ol>{top}</ol><p class="note" style="margin-top:12px">{html.escape(RUNNER_UP)}</p></section>
{colour_section(reds, rows)}
<h2 style="font-size:18px;margin:0">All 10</h2>
<div class="overview">{overview}</div>
{cards}
</main></body></html>
"""


def main():
    problems, count = write_files()
    gtk, sheet = gtk_proof()
    reds, rows = colour_study()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(gallery(gtk, problems, count, reds, rows))
    print(f"wrote {count} r2 svg files under {LOGOS}")
    print(f"checks: {'all pass' if not problems else problems}")
    for key, colour, label, (nearest, red), vs_default in rows:
        print(f"heart {key:5s} {colour}: nearest app error red {red} dE2000 {nearest:.1f}; vs {APP_RED} {vs_default:.1f}")
    print(f"gtk sheet: {sheet}")
    print(f"gallery: {REPORT} ({REPORT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
