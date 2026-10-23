#!/usr/bin/env python3
"""Build the 10 round-4 RobotDx logo variants and their gallery (2026-10-23).

Round 4 takes Zach's round-3 pick, r3-01 Big Sticker, and makes 10 ORTHOGONAL variants of it (2026-10-23: "Ok I like
the big sticker, do 10 more variants on that make them orthogonal variants"). Each variant r4-NN changes exactly ONE
design axis against r3-01 and holds everything else at r3-01's value; no two variants move the same axis, so the
winners can be combined ("r4-03's heart position + r4-07's tile").

How orthogonality is enforced: r3-01 is a set of parameters (REF_FULL / REF_SYM below) and one builder, mark(), draws
any parameter set. A variant is `mark` with ONE override (the AXES table says which). At run time mark() with no
override is asserted to reproduce build_logos_r3.c01 byte for byte in every state, so "everything else" really is
r3-01. The face is not a parameter at all: it is the same face_el call in every mark, and write_files()
asserts the face's markup is present, unchanged, in every file.

Rules carried from rounds 2-3:
  - the calm two-dot face (two eyes joined by a straight line) never changes, in any mark or state;
  - health shows through the heart (the life meter: full / half / broken / dotted) or the app's own status badges;
  - an original mark: no Disney silhouette or artwork;
  - the heart is rose #f483a5.

The app is being renamed RobotDx ("from robotdx import RobotDiagnostics"; app search "robotdx"), so SVG titles say
"RobotDx - <name>".

Writes, for every variant r4-NN-<slug>:
  brand/logos/r4-NN-<slug>.svg               full-colour mark, 64 grid
  brand/logos/r4-NN-<slug>-symbolic.svg      22 px monochrome top-bar icon (filled rect / circle / path only)
  brand/logos/states/r4-NN-<slug>-<state>.svg and -<state>-symbolic.svg   warn / error / stale
and the self-contained gallery reports/media/robotdiagnostics-logos-round4-2026-10-23.html (r3-01 beside every variant).

Geometry, checkers, the meter and the gallery chrome come from rounds 1-3 (imported and left untouched).

Run: python3 /home/bam/io-diagnostics/brand/build_logos_r4.py
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
import build_logos_r2 as r2  # noqa: E402
import build_logos_r3 as r3  # noqa: E402
from build_logos import EDGE, INK, LOGOS, STATES_DIR, densify, face_el, fill, path, rect  # noqa: E402
from build_logos_r2 import (ROSE, STICKER, WHITE, gapped_ring, heart_centre, heart_shape, ind, inside,  # noqa: E402
                            meter, piece, poly_d, rotate, rr_pts)

REPORT = pathlib.Path("/home/bam/io-diagnostics/reports/media/robotdiagnostics-logos-round4-2026-10-23.html")
SHOTS = pathlib.Path("/home/bam/io-diagnostics/reports/media/round4")
STATES = ("ok", "warn", "error", "stale")
NAME = "RobotDx"

assert r2.PAL["heart"] == ROSE == "#f483a5"

# ---------------------------------------------------------------- r3-01, as parameters
# (the numbers are build_logos_r3.c01's, unchanged)

HEAD = (4, 7, 50, 42)                      # the head's box on the 64 grid; no variant moves or resizes it
HEAD_R = 16
FACE = (28.5, 26.5, 12.5, 5.4, 3.3)        # the face: same call, same numbers, in every full mark
REF_FULL = dict(
    stroke=2.0,          # head outline weight
    squircle=None,       # None = rounded rectangle; a number = superellipse exponent
    shade=False,         # soft cushion shading on the head
    hd=8.9, rot=-10, grow=2.6, meter_w=2.5,
    hx=47, hb=59.5,      # r3-01's heart; the centre is what is placed (see centre_of)
    turn=0.0, push=0.0,  # placement: degrees round the head from r3-01's lower-right, and extra distance outward
    vinyl=False,         # thick die-cut border + soft drop shadow
    pixel=False,         # video-game heart
    tile=False,          # rose dock tile behind a white head
    crop=None,           # close crop: scale factor
)
SYM_RING = (1.6, 3.3, 16.9, 13.7, 4.9)
SYM_FACE = (9.9, 9.6, 4.3, 2.05, 1.45)
REF_SYM = dict(
    stroke=1.75, squircle=None, shade=False, hd=4.3, rot=-10, gap=1.85, meter_w=1.2, hx=16.0, hb=21.75,
    turn=0.0, push=0.0, vinyl=False, pixel=False, tile=False, crop=None,
)


# ---------------------------------------------------------------- geometry

def superellipse(x, y, w, h, exponent, step=0.3):
    """|dx/a|^n + |dy/b|^n = 1 as a dense clockwise outline: n = 2 is an ellipse, large n a rounded square."""
    a, b = w / 2, h / 2
    count = max(160, int(2 * math.pi * max(a, b) / step))
    pts = []
    for i in range(count):
        t = 2 * math.pi * i / count
        c, s = math.cos(t), math.sin(t)
        pts.append((x + a + a * math.copysign(abs(c) ** (2 / exponent), c), y + b + b * math.copysign(abs(s) ** (2 / exponent), s)))
    return densify(pts + pts[:1], step)[:-1]


def outline_of(box, radius, squircle):
    x, y, w, h = box
    return superellipse(x, y, w, h, squircle) if squircle else rr_pts(x, y, w, h, radius)


def ray_hit(outline, origin, angle):
    """Distance from origin, along angle, to where the ray leaves the outline."""
    dx, dy = math.cos(angle), math.sin(angle)
    lo, hi = 0.0, 1.0
    while inside((origin[0] + dx * hi, origin[1] + dy * hi), outline):
        lo, hi = hi, hi * 1.5
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if inside((origin[0] + dx * mid, origin[1] + dy * mid), outline) else (lo, mid)
    return (lo + hi) / 2


def centre_of(ref, box, radius, p):
    """Where the heart's centre goes. r3-01 puts it a fixed distance outside the head's outline along one ray from the
    head's centre; every variant keeps that distance, so a different head shape or a turn round the head moves the
    heart with the outline rather than leaving it floating (`push` adds distance outward, `turn` swings the ray)."""
    x, y, w, h = box
    head_c = (x + w / 2, y + h / 2)
    ref_c = heart_centre(ref["hx"], ref["hb"], ref["hd"])
    angle0 = math.atan2(ref_c[1] - head_c[1], ref_c[0] - head_c[0])
    delta = math.dist(ref_c, head_c) - ray_hit(rr_pts(x, y, w, h, radius), head_c, angle0)
    angle = angle0 + math.radians(p["turn"])
    reach = ray_hit(outline_of(box, radius, p["squircle"]), head_c, angle) + delta + p["push"]
    return head_c[0] + math.cos(angle) * reach, head_c[1] + math.sin(angle) * reach


def bbox(points):
    xs, ys = [q[0] for q in points], [q[1] for q in points]
    return min(xs), min(ys), max(xs), max(ys)


def nudge(box, limit, slack, margin):
    """Keep a box inside [0, limit]: left alone while it is within `slack` of the edge (r3-01's own tip is 0.4 off the
    bottom), else the smallest uniform shrink + shift that leaves it `margin` clear of every edge. None if untouched."""
    x0, y0, x1, y1 = box
    if min(x0, y0) >= slack and max(x1, y1) <= limit - slack:
        return None
    room = limit - 2 * margin
    scale = min(1.0, room / (x1 - x0), room / (y1 - y0))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    sx0, sx1 = cx + (x0 - cx) * scale, cx + (x1 - cx) * scale
    sy0, sy1 = cy + (y0 - cy) * scale, cy + (y1 - cy) * scale
    dx = max(0.0, margin - sx0) - max(0.0, sx1 - (limit - margin))
    dy = max(0.0, margin - sy0) - max(0.0, sy1 - (limit - margin))
    return scale, cx * (1 - scale) + dx, cy * (1 - scale) + dy


def contain(box, target):
    """Scale + shift that centres a box inside a target box (never enlarging)."""
    x0, y0, x1, y1 = box
    tx0, ty0, tx1, ty1 = target
    scale = min(1.0, (tx1 - tx0) / (x1 - x0), (ty1 - ty0) / (y1 - y0))
    return scale, (tx0 + tx1) / 2 - scale * (x0 + x1) / 2, (ty0 + ty1) / 2 - scale * (y0 + y1) / 2


def union_outline(a, b):
    """a + b as one outline, for two dense clockwise outlines that cross exactly twice (the head and a sticker over its
    corner): a's run outside b, then b's run outside a."""
    keep_a, keep_b = [not inside(q, b) for q in a], [not inside(q, a) for q in b]
    starts_a = [k for k in range(len(a)) if keep_a[k] and not keep_a[k - 1]]
    starts_b = [k for k in range(len(b)) if keep_b[k] and not keep_b[k - 1]]
    assert len(starts_a) == len(starts_b) == 1, "union_outline handles one crossing pair"
    run_a = [a[k] for k in r3._run(keep_a, starts_a[0])]
    run_b = [b[k] for k in r3._run(keep_b, starts_b[0])]
    if math.dist(run_b[0], run_a[-1]) > math.dist(run_b[-1], run_a[-1]):
        run_b.reverse()
    return run_a + run_b


def wrap(inner, moved):
    if not moved:
        return inner
    scale, tx, ty = moved
    return f'<g transform="translate({r1.fmt(tx)} {r1.fmt(ty)}) scale({r1.fmt(scale)})">{inner}</g>'


FITS: dict = {}   # (uid, "full" | "sym") -> the shrink + shift the fit applied, for the gallery's notes


# ---------------------------------------------------------------- the pixel heart (axis 9)

PIXEL_ROWS = ["·XXX·XXX·", "XXXXXXXXX", "XXXXXXXXX", "XXXXXXXXX", "·XXXXXXX·", "··XXXXX··", "···XXX···", "····X····"]
PIXEL_CELLS = frozenset((r, c) for r, row in enumerate(PIXEL_ROWS) for c, ch in enumerate(row) if ch == "X")
# the 20 cells of the heart's outline in clockwise order from the top-left lobe; every other one is a dot when stale
PIXEL_RING = [(0, 1), (0, 2), (0, 3), (1, 4), (0, 5), (0, 6), (0, 7), (1, 8), (2, 8), (3, 8), (4, 7), (5, 6), (6, 5),
              (7, 4), (6, 3), (5, 2), (4, 1), (3, 0), (2, 0), (1, 0)]
# the crack's column in each row; the right half drops one row when the heart is broken
CRACK = [4, 4, 3, 5, 4, 3, 5, 4]


def dilate(cells):
    return {(r + dr, c + dc) for r, c in cells for dr, dc in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))}


def cells_loops(cells, size, origin):
    """The boundary of a set of pixels as closed outlines (clockwise round solid, anticlockwise round holes), found by
    cancelling every shared edge - so a sticker can carry ONE outline round a pixel heart instead of a seam per pixel."""
    edges = set()
    for r, c in cells:
        for a, b in (((c, r), (c + 1, r)), ((c + 1, r), (c + 1, r + 1)), ((c + 1, r + 1), (c, r + 1)), ((c, r + 1), (c, r))):
            if (b, a) in edges:
                edges.discard((b, a))
            else:
                edges.add((a, b))
    out_of = {}
    for a, b in edges:
        out_of.setdefault(a, []).append(b)
    loops = []
    while out_of:
        start = next(iter(out_of))
        loop, here, heading = [start], start, None
        while True:
            options = out_of[here]
            if len(options) > 1 and heading is not None:   # a corner where two pixels only touch: turn right, hug the pixel
                options = sorted(options, key=lambda b: -(heading[0] * (b[1] - here[1]) - heading[1] * (b[0] - here[0])))
            nxt = options[0]
            out_of[here] = [b for b in out_of[here] if b != nxt]
            if not out_of[here]:
                del out_of[here]
            heading = (nxt[0] - here[0], nxt[1] - here[1])
            here = nxt
            if here == start:
                break
            loop.append(here)
        loops.append([(origin[0] + x * size, origin[1] + y * size) for x, y in loop])
    return loops


class VectorHeart:
    """r3-01's heart: round 1's heart outline, tilted, on a white die-cut sticker."""

    def __init__(self, centre, hd, rot, meter_w):
        self.hd, self.rot, self.meter_w = hd, rot, meter_w
        self.cx, self.hb = centre[0], centre[1] + 1.1 * hd

    def sticker_pts(self, grow):
        return heart_shape(self.cx, self.hb, self.hd, rot=self.rot, grow=grow)

    def sticker_d(self, grow):
        return poly_d(self.sticker_pts(grow))

    def near(self, clear):
        zone = self.sticker_pts(clear)
        return lambda point: inside(point, zone)

    def meter(self, state, sym):
        return meter(self.cx, self.hb, self.hd, state, self.meter_w, sym, rot=self.rot)

    def cover_pts(self):
        """Everything any state can reach: the stale dots sit just outside the outline, the broken halves splay."""
        pts = self.sticker_pts(self.meter_w + 0.4)
        for d, _evenodd in r2.meter_pieces(self.cx, self.hb, self.hd, "error", self.meter_w, rot=self.rot):
            nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", d)]
            pts += list(zip(nums[0::2], nums[1::2]))
        return pts


class PixelHeart:
    """A video-game heart: 9 x 8 square pixels, drawn as full / half / broken / dotted."""

    def __init__(self, origin, size, rot):
        self.origin, self.size, self.rot = origin, size, rot
        self.centre = (origin[0] + 4.5 * size, origin[1] + 4 * size)

    def loops(self, cells):
        loops = cells_loops(cells, self.size, self.origin)
        return [[rotate(q, self.centre, self.rot) for q in loop] for loop in loops] if self.rot else loops

    def d(self, cells):
        return "".join(poly_d(loop) for loop in self.loops(cells))

    def sticker_pts(self, rim=1):
        cells = PIXEL_CELLS
        for _ in range(rim):
            cells = dilate(cells)
        return self.loops(cells)[0]

    def sticker_d(self, rim=1):
        return poly_d(self.sticker_pts(rim))

    def near(self, clear):
        """Within `clear` of any pixel - the gap a symbolic outline leaves round the heart."""
        size, (ox, oy) = self.size, self.origin

        def blocked(point):
            x, y = rotate(point, self.centre, -self.rot) if self.rot else point
            return any(math.hypot(max(ox + c * size - x, 0, x - ox - (c + 1) * size),
                                  max(oy + r * size - y, 0, y - oy - (r + 1) * size)) < clear for r, c in PIXEL_CELLS)
        return blocked

    def pieces(self, state):
        if state == "ok":
            return [self.d(PIXEL_CELLS)]
        if state == "warn":   # half: the left half solid, the right half only its outline
            ring = {cell for cell in PIXEL_RING if cell[1] >= 5}
            return [self.d({cell for cell in PIXEL_CELLS if cell[1] <= 4} | ring)]
        if state == "error":  # broken: a zigzag crack, the right half dropped one pixel
            left = {(r, c) for r, c in PIXEL_CELLS if c < CRACK[r]}
            right = {(r + 1, c) for r, c in PIXEL_CELLS if c > CRACK[r]}
            return [self.d(left), self.d(right)]
        if state == "stale":  # dotted: every other pixel of the outline
            return [self.d(set(PIXEL_RING[::2]))]
        raise ValueError(state)

    def meter(self, state, sym):
        return "".join(piece((d, False), ind(state, sym)) for d in self.pieces(state))


# ---------------------------------------------------------------- the mark

def heart_for(p, sym, centre):
    if p["pixel"]:
        if sym:   # pixels only stay crisp on the grid: whole-pixel size and origin, and upright (see AXES, axis 9)
            return PixelHeart((round(centre[0] - 4.5), round(centre[1] - 4)), 1, 0)
        return PixelHeart((centre[0] - 4.5 * 2.4, centre[1] - 4 * 2.4), 2.4, p["rot"])
    return VectorHeart(centre, p["hd"], p["rot"], p["meter_w"])


def mark(state, sym, uid="r4", **override):
    """The r3-01 mark with the overrides applied: exactly one axis, per the AXES table."""
    p = {**(REF_SYM if sym else REF_FULL), **override}
    return mark_sym(state, p, uid) if sym else mark_full(state, p, uid)


def mark_full(state, p, uid):
    x, y, w, h = HEAD
    outline = outline_of(HEAD, HEAD_R, p["squircle"])
    heart = heart_for(p, False, centre_of(REF_FULL, HEAD, HEAD_R, p))

    defs = ""
    head_fill = WHITE
    if p["shade"]:
        head_fill = f"url(#{uid}h)"
        defs += (f'<radialGradient id="{uid}h" cx="0.38" cy="0.3" r="0.9"><stop offset="0" stop-color="#ffffff"/>'
                 '<stop offset="0.5" stop-color="#f0f2f5"/><stop offset="1" stop-color="#c3cbd5"/></radialGradient>')
    attrs = f' fill="{head_fill}" stroke="{EDGE}" stroke-width="{r1.fmt(p["stroke"])}"'
    head_el = rect(x, y, w, h, HEAD_R, attrs) if not p["squircle"] else path(poly_d(outline), attrs + ' stroke-linejoin="round"')
    face = face_el(FACE[0], FACE[1], FACE[2], FACE[3], FACE[4], "flat", fill(INK))

    grow = 4.2 if p["vinyl"] else p["grow"]
    pad = p["stroke"] / 2
    covered = [(x - pad, y - pad), (x + w + pad, y + h + pad)]
    if p["pixel"]:
        sticker, sticker_pts = path(heart.sticker_d(), STICKER), heart.sticker_pts()
    else:
        sticker, sticker_pts = path(heart.sticker_d(grow), STICKER), heart.sticker_pts(grow + 0.6)
    shadow = ""
    if p["vinyl"]:
        defs += f'<filter id="{uid}b" x="-30%" y="-30%" width="170%" height="170%"><feGaussianBlur stdDeviation="1.4"/></filter>'
        shifted = [(q[0] + 0.7, q[1] + 2.0) for q in heart.sticker_pts(grow)]
        shadow = f'<path d="{poly_d(shifted)}" fill="#0b1f33" opacity="0.34" filter="url(#{uid}b)"/>'
        covered += [(q[0] + 2.6, q[1] + 2.6) for q in shifted]
    covered += sticker_pts

    body = (f"<defs>{defs}</defs>" if defs else "") + head_el + face + shadow + sticker + heart.meter(state, False)
    box = bbox(covered)
    if p["tile"]:
        moved = contain(box, (9, 9, 55, 55))
        FITS[(uid, "full")] = moved
        return f'<rect x="4" y="4" width="56" height="56" rx="14" fill="{ROSE}"/>' + wrap(body, moved)
    if p["crop"]:   # bleed off the top-left, heart and eyes still whole
        scale = p["crop"]
        return wrap(body, (scale, 60.4 * (1 - scale), 63.4 * (1 - scale)))
    moved = nudge(box, 64.0, 0.15, 0.5)
    if moved:
        FITS[(uid, "full")] = moved
    return wrap(body, moved)


def mark_sym(state, p, uid):
    x, y, w, h, r = SYM_RING
    heart = heart_for(p, True, centre_of(REF_SYM, (x, y, w, h), r, p))
    face = face_el(SYM_FACE[0], SYM_FACE[1], SYM_FACE[2], SYM_FACE[3], SYM_FACE[4], "flat")
    outline = outline_of((x, y, w, h), r, p["squircle"])
    half = p["stroke"] / 2
    # WHY every state's reach: the fit must not move the mark when the state changes, and the broken heart splays
    covered = [(x - half, y - half), (x + w + half, y + h + half)] + (heart.sticker_pts(1) if p["pixel"] else heart.cover_pts())

    if p["tile"]:   # inversion in one colour: a solid tile with the white head + sticker cut out, the face and heart inside
        union = union_outline(outline, heart.sticker_pts(1) if p["pixel"] else heart.sticker_pts(1.35))
        moved = contain(bbox(union), (2.4, 2.4, 19.6, 19.6))
        FITS[(uid, "sym")] = moved
        scale, tx, ty = moved
        hole = [(scale * px + tx, scale * py + ty) for px, py in union]
        return (piece((poly_d(rr_pts(0.5, 0.5, 21, 21, 5.5)) + poly_d(hole), True))
                + wrap(face + heart.meter(state, True), moved))
    gap = 2.9 if p["vinyl"] else p["gap"]
    ring = piece(gapped_ring(outline, p["stroke"], heart.near(gap)))
    tone = ""
    if p["shade"]:   # depth in one colour: the head's interior at a low opacity of the panel's ink
        tone = piece(r3.subtract(outline, heart.sticker_pts(1) if p["pixel"] else heart.sticker_pts(1.6)), ' opacity="0.16"')
    body = tone + ring + face + heart.meter(state, True)

    if p["crop"]:   # scaled about the heart's far corner, so the head bleeds a little off the top-left like the full mark
        scale = p["crop"]
        _x0, _y0, x1, y1 = bbox(covered)
        return wrap(body, (scale, x1 * (1 - scale), y1 * (1 - scale)))
    moved = nudge(bbox(covered), 22.0, -SYM_SPILL, 0.3)
    if moved:
        FITS[(uid, "sym")] = moved
    return wrap(body, moved)


def _sym_spill():
    """How far r3-01's own 22 px icon reaches past the box in its worst state (its broken heart): a variant is only
    refit when it spills further than the reference does."""
    p = dict(REF_SYM)
    x, y, w, h, r = SYM_RING
    heart = heart_for(p, True, centre_of(REF_SYM, (x, y, w, h), r, p))
    x0, y0, x1, y1 = bbox(heart.cover_pts() + [(x, y), (x + w, y + h)])
    return max(0.0, -x0, -y0, x1 - 22.0, y1 - 22.0)


SYM_SPILL = _sym_spill()

# ---------------------------------------------------------------- the axes

# num, slug, name, axis, r3-01's value, this variant's value, why, overrides
AXES = [
    (1, "hud-corner", "HUD Corner", "Heart placement", "Lower-right corner, hanging off the head's bottom edge",
     "Upper-left corner, hanging off the head's top edge",
     "Where a game keeps its life hearts: the top-left of the screen. The mark's weight moves to the top, away from the chin.",
     dict(both=dict(turn=180.0))),
    (2, "hero-heart", "Hero Heart", "Heart scale", "Heart 8.9 (half-diagonal), head : heart about 5.6 : 1",
     "Heart 12.9, 45 % bigger (head : heart about 3.9 : 1); pushed outward so it clears the eye",
     "The sticker becomes the hero and the head the supporting shape: the life meter is what survives at 32 px and below.",
     dict(full=dict(hd=12.9, push=3.4, meter_w=3.4), sym=dict(hd=5.9, push=1.3, meter_w=1.7))),
    (3, "upright-heart", "Upright Heart", "Heart rotation", "Tilted -10 degrees", "Upright, 0 degrees",
     "An upright heart reads as an instrument (a gauge) rather than a decoration slapped on, and its sides land on the 22 px grid.",
     dict(both=dict(rot=0))),
    (4, "vinyl-sticker", "Vinyl Sticker", "Sticker material", "Thin white border (2.6), flat, no shadow",
     "Thick die-cut border (4.2) and a soft drop shadow; the 22 px icon leaves a wider cut-out instead",
     "Reads as a real vinyl sticker stuck on the head. A 22 px mono icon cannot carry a shadow, so its die-cut gap gets wider.",
     dict(both=dict(vinyl=True))),
    (5, "squircle-head", "Squircle Head", "Head shape", "Rounded rectangle, corner radius 16",
     "Wide squircle (superellipse, exponent 2.2, nearly an ellipse) in the same box; the heart keeps its distance from the outline",
     "A softer, rounder head with no corners; the heart still hangs off the same stretch of the outline.",
     dict(both=dict(squircle=2.2))),
    (6, "bold-outline", "Bold Outline", "Head outline weight", "2 px grey rim (1.75 px at 22)",
     "4 px grey rim (2.8 px at 22), same colour",
     "A chunkier rim carries the shape in the dock and beside bold top-bar text; the head reads as an object.",
     dict(full=dict(stroke=4.0), sym=dict(stroke=2.8))),
    (7, "rose-tile", "Rose Tile", "Colour inversion", "White head on the page, no tile",
     "White head on a rose dock tile; the 22 px icon is a solid tile with the white head and sticker cut out of it",
     "The brand colour becomes the ground, so RobotDx is a coloured tile in the dock; the white sticker rim keeps the rose heart visible on it.",
     dict(both=dict(tile=True))),
    (8, "close-crop", "Close Crop", "Framing", "Whole head inside the frame",
     "Cropped in 15 % (7 % at 22 px): the head bleeds off the top-left of the frame, heart and eyes still whole",
     "A head bigger than the frame is bolder at 16-32 px: the face and the heart are all there is to read.",
     dict(full=dict(crop=1.15), sym=dict(crop=1.07))),
    (9, "pixel-heart", "Pixel Heart", "Heart drawing style", "Smooth vector heart",
     "9 x 8 pixel heart, drawn full / half / broken / dotted on the pixel grid (upright at 22 px)",
     "The video-game heart Zach liked in round 2, and the only style that is exact at 22 px: whole pixels on whole pixels.",
     dict(both=dict(pixel=True))),
    (10, "soft-cushion", "Soft Cushion", "Depth", "Flat white head",
     "Head shaded like a cushion (white centre, cool grey edge); the 22 px icon gets a faint tone inside the rim",
     "Adds the soft, inflated feel of the character it nods to without changing a line of the geometry.",
     dict(both=dict(shade=True))),
]
# The suggested axis 11, "sticker peel" (a lifted, curled corner), was dropped: the peel is drawn on the heart's own
# outline, so it changes how the meter's half / broken / dotted states look - a second axis - and it overlaps 04.


def overrides(entry, sym):
    """The one axis's override for the 64 grid or the 22 grid. Sizes are grid-specific, so an axis that has one gives
    both (`full=`, `sym=`); the rest are the same value on both (`both=`)."""
    over = entry[-1]
    return {**over.get("both", {}), **over.get("sym" if sym else "full", {})}


def builder(num):
    entry = next(e for e in AXES if e[0] == num)

    def build(state, sym):
        return mark(state, sym, uid=f"r4k{num}{state}", **overrides(entry, sym))
    return build


def stem_of(entry):
    return f"r4-{entry[0]:02d}-{entry[1]}"


# ---------------------------------------------------------------- the face is never touched, the baseline is r3-01

def check_baseline():
    """mark() with no override reproduces r3-01 byte for byte, in every state, full and symbolic."""
    for state in STATES:
        for sym in (False, True):
            assert mark(state, sym) == r3.c01(state, sym), (state, sym)


FACE_FULL = face_el(FACE[0], FACE[1], FACE[2], FACE[3], FACE[4], "flat", fill(INK))
FACE_SYM = face_el(SYM_FACE[0], SYM_FACE[1], SYM_FACE[2], SYM_FACE[3], SYM_FACE[4], "flat")


def check_face(text, sym):
    """The face is the same markup in every file."""
    return [] if (FACE_SYM if sym else FACE_FULL) in text else ["face changed"]


# ---------------------------------------------------------------- contrast

def luminance(colour):
    channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# colours, what the pair is, floor. Ink on the head is text-grade (the face is the mark's content); the rest are
# graphics, held at r3-01's own value because the brand fixes rose and white.
CONTRAST = [
    (INK, WHITE, "face on the white head", 7.0),
    (INK, "#c3cbd5", "face on the cushion's darkest edge (r4-10)", 7.0),
    (ROSE, WHITE, "rose heart on its white sticker (the same pair as r3-01)", 2.3),
    (ROSE, "#1d1f22", "rose heart on a dark dock or panel", 3.0),
    (WHITE, ROSE, "white head and sticker rim on the rose tile (r4-07)", 2.3),
    (EDGE, WHITE, "the head's grey rim on a white page (what keeps the head visible there)", 1.5),
]


def contrast_rows():
    return [(label, contrast(a, b), minimum, a, b) for a, b, label, minimum in CONTRAST]


# ---------------------------------------------------------------- files

def write_files():
    LOGOS.mkdir(parents=True, exist_ok=True)
    STATES_DIR.mkdir(parents=True, exist_ok=True)
    problems, count = {}, 0
    for entry in AXES:
        num, name = entry[0], entry[2]
        build = builder(num)
        stem = stem_of(entry)
        for state in STATES:
            suffix = "" if state == "ok" else f" ({state})"
            full = r1.svg_file(build(state, False), 64, f"{NAME} - {name}{suffix}")
            sym = r1.svg_file(build(state, True), 22, f"{NAME} - {name} symbolic{suffix}", symbolic=True)
            full_path = LOGOS / f"{stem}.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}.svg"
            sym_path = LOGOS / f"{stem}-symbolic.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}-symbolic.svg"
            full_path.write_text(full)
            sym_path.write_text(sym)
            count += 2
            found = (r1.check_full(full) + r1.check_symbolic(sym) + check_face(full, False)
                     + check_face(sym, True))
            if found:
                problems[f"{stem}/{state}"] = found
    return problems, count


def sym_file(stem, state):
    return LOGOS / f"{stem}-symbolic.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}-symbolic.svg"


def rows_for_gtk():
    return [("ref", "r3-01-big-sticker")] + [(entry[0], stem_of(entry)) for entry in AXES]


def gtk_proof():
    """Each symbolic file (r3-01's too) through Gtk.IconInfo.load_symbolic at 22 px, the path GNOME uses to recolour it."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf, Gio, Gtk

    def rgba(text):
        colour = Gdk.RGBA()
        colour.parse(text)
        return colour

    themes = {"dark": ("#ffffff", "#ffb000", "#ff4d4d", "#1d1d1d"), "light": ("#2e3436", "#b35c00", "#c00000", "#fafafa")}
    theme = Gtk.IconTheme.new()
    out = {}
    scale = 4
    cell = 22 * scale + 12
    rows = rows_for_gtk()
    sheet = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, cell * 8 + 12, cell * len(rows) + 12)
    sheet.fill(0x808080ff)
    for row, (key, stem) in enumerate(rows):
        for col, (theme_name, (fg, warn, err, bg)) in enumerate(themes.items()):
            for k, state in enumerate(STATES):
                file = sym_file(stem, state)
                info = theme.lookup_by_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(file))), 22,
                                             Gtk.IconLookupFlags.FORCE_SIZE)
                pix, was_symbolic = info.load_symbolic(rgba(fg), rgba(fg), rgba(warn), rgba(err))
                assert was_symbolic, file
                assert (pix.get_width(), pix.get_height()) == (22, 22), file
                ok, data = pix.save_to_bufferv("png", [], [])
                out[(key, state, theme_name)] = base64.b64encode(data).decode()
                tile = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 22 * scale, 22 * scale)
                tile.fill(int(bg[1:], 16) << 8 | 0xff)
                big = pix.scale_simple(22 * scale, 22 * scale, GdkPixbuf.InterpType.NEAREST)
                big.composite(tile, 0, 0, 22 * scale, 22 * scale, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
                tile.copy_area(0, 0, 22 * scale, 22 * scale, sheet, 12 + (col * 4 + k) * cell, 12 + row * cell)
    SHOTS.mkdir(parents=True, exist_ok=True)
    sheet_path = SHOTS / "logos-r4-gtk-symbolic-sheet.png"
    sheet.savev(str(sheet_path), "png", [], [])
    return out, sheet_path


# ---------------------------------------------------------------- gallery

def sprite():
    symbols = []

    def add(key, build):
        for state in STATES:
            symbols.append(f'<symbol id="f{key}-{state}" viewBox="0 0 64 64">{build(state, False)}</symbol>')
            symbols.append(f'<symbol id="s{key}-{state}" viewBox="0 0 22 22"><g fill="currentColor">'
                           f'{r2.sym_symbol(build(state, True))}</g></symbol>')
    add("ref", r3.c01)
    for entry in AXES:
        add(entry[0], builder(entry[0]))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true">'
            + "".join(symbols) + "</svg>")


use = r1.use


def column(key, label, gtk, accent=False):
    big = f'<figure>{use(f"f{key}-ok", 256)}<figcaption>256</figcaption></figure>'
    sizes = "".join(f'<figure>{use(f"f{key}-ok", n)}<figcaption>{n}</figcaption></figure>' for n in (64, 32, 22))
    sym = f'<figure class="sym">{use(f"s{key}-ok", 22)}<figcaption>22 sym</figcaption></figure>'
    ladders = (f'<div class="ladder light r4big">{big}<div class="r4small">{sizes}{sym}</div></div>'
               f'<div class="ladder dark r4big">{big}<div class="r4small">{sizes}{sym}</div></div>')
    bars = (r1.topbar(key, "ok", "IO ✓", "dark") + r1.topbar(key, "warn", "IO 3 ⚠", "dark")
            + r1.topbar(key, "ok", "IO ✓", "light") + r1.topbar(key, "error", "IO 1 ✖", "light"))
    docks = f'<div class="docks">{r1.dock(key, "dark")}{r1.dock(key, "light")}</div>'
    figs = "".join(f'<figure>{use(f"f{key}-{s}", 64)}<figcaption>{s}</figcaption></figure>' for s in STATES)
    states = f'<div class="ladder light st">{figs}</div><div class="ladder dark st">{figs}</div>'

    def proof(theme):
        return "".join(f'<figure><img class="zoom" src="data:image/png;base64,{gtk[(key, s, theme)]}" width="66" height="66" alt="{s}">'
                       f'<figcaption>{s}</figcaption></figure>' for s in STATES)
    gtk_rows = f'<div class="gtk-row dark vs-gtk">{proof("dark")}</div><div class="gtk-row light vs-gtk">{proof("light")}</div>'
    return (f'<div class="vs-col{" acc" if accent else ""}"><h4>{label}</h4>{ladders}'
            f'<h5>GNOME top bar - dark and light</h5>{bars}<h5>Dock tile</h5>{docks}'
            f'<h5>Health states, full colour</h5>{states}<h5>The real 22 px GTK icon - ok · warn · error · stale</h5>{gtk_rows}</div>')


def fit_note(num):
    notes = []
    for kind, label in (("full", "full mark"), ("sym", "22 px icon")):
        moved = FITS.get((f"r4k{num}ok", kind))
        if moved:
            scale, tx, ty = moved
            what = "fitted into the tile" if num == 7 else "kept inside the frame"
            notes.append(f"{label} {what}: scale {scale:.2f}, shift ({tx:+.1f}, {ty:+.1f}) px")
    return ("Layout only, no axis: " + "; ".join(notes) + ".") if notes else ""


def card(entry, gtk):
    num, slug, name, axis, ref_value, new_value, why, _over = entry
    stem = stem_of(entry)
    note = fit_note(num)
    return f"""
<section class="card" id="logo-{num:02d}">
  <header>
    <div class="num">{num:02d}</div>
    <div class="title"><h2>{html.escape(name)}</h2><p class="idea">{html.escape(why)}</p></div>
    <div class="meta"><span class="family">axis: {html.escape(axis)}</span></div>
  </header>
  <p class="axisline"><b>{html.escape(axis)}</b> - r3-01: {html.escape(ref_value)}. This variant: {html.escape(new_value)}.
  {html.escape(note)}</p>
  <div class="vs n2">{column("ref", "Reference - r3-01 Big Sticker", gtk)}{column(num, f"r4-{num:02d} {html.escape(name)}", gtk, True)}</div>
  <p class="files"><code>brand/logos/{stem}.svg</code> · <code>brand/logos/{stem}-symbolic.svg</code> · states <code>brand/logos/states/{stem}-{{warn,error,stale}}[-symbolic].svg</code></p>
</section>"""


EXTRA_CSS = r3.EXTRA_CSS + """
.axes{width:100%;border-collapse:collapse;font-size:13.5px;margin:0 0 6px;background:var(--surface);border:1px solid var(--border);border-radius:12px;overflow:hidden}
.axes th,.axes td{padding:9px 12px;text-align:left;vertical-align:top;border-bottom:1px solid var(--border)}
.axes th{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--faint);font-weight:600}
.axes tr:last-child td{border-bottom:0}
.axes td:first-child a{color:var(--text);text-decoration:none;display:flex;align-items:center;gap:10px}
.axes td:first-child .ic{background:#fff;border-radius:8px;padding:3px;flex:none;box-sizing:content-box}
.axes td.val{color:var(--muted)}
.axisline{margin:10px 0 14px;font-size:13.5px;color:var(--muted)}
.axisline b{color:var(--text)}
.vs.n2{grid-template-columns:1fr 1fr}
.vs-col.acc{border-color:var(--accent)}
.vs-col h5{margin:12px 0 6px;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--faint)}
.r4big{align-items:flex-end;flex-wrap:nowrap}
.r4small{display:flex;align-items:flex-end;gap:12px;flex-wrap:wrap}
.vs-col .ladder.st{gap:12px;padding:10px}
.vs-col .topbar{font-size:12px}
.gtk-row.vs-gtk + .gtk-row.vs-gtk{margin-top:6px}
.contrast{width:100%;border-collapse:collapse;font-size:13px}
.contrast td,.contrast th{padding:6px 10px;border-bottom:1px solid var(--border);text-align:left}
.contrast .pass{color:#1f7a4d;font-weight:600}
.contrast .fail{color:#c00000;font-weight:600}
.swatch{display:inline-block;width:14px;height:14px;border-radius:3px;border:1px solid rgba(0,0,0,.2);margin-right:3px;vertical-align:middle}
@media (max-width:820px){.vs.n2{grid-template-columns:1fr}.axes td:nth-child(3),.axes th:nth-child(3){display:none}
.r4big{flex-wrap:wrap}.ladder svg[width="256"]{width:160px;height:160px}}
"""

TOP3 = [
    (9, "Pixel Heart", "The only heart that is exact at 22 px: 9 x 8 whole pixels on whole pixels, so full / half / broken / dotted "
        "are crisp in the real GTK render (see the 22 px rows) where the vector heart's half and dots blur. It is also the "
        "video-game heart Zach liked in round 2, and it changes nothing else."),
    (2, "Hero Heart", "The heart is the life meter, so making it the biggest thing in the mark makes the states readable at "
        "32 px and in the top bar (the 22 px heart is about 14 px wide instead of 10.4); the head shrinks into a supporting shape and the "
        "face is untouched."),
    (7, "Rose Tile", "The one variant that changes how the app looks in the dock: a rose tile with the white head and sticker on it "
        "is distinct from every other icon, and the white sticker rim keeps the rose heart visible on the rose ground. The 22 px "
        "icon is the busiest of the set, so combine this tile with 09's pixel heart for the top bar."),
]


def gallery(gtk, problems, count):
    by_num = {entry[0]: entry for entry in AXES}
    overview = "".join(f'<a href="#logo-{n:02d}"><span class="pair">{use(f"f{n}-ok", 48)}{use(f"s{n}-ok", 22, "symb")}</span>'
                       f'{n:02d} {html.escape(by_num[n][2])}</a>' for n in sorted(by_num))
    ref = f'<a><span class="pair">{use("fref-ok", 48)}{use("sref-ok", 22, "symb")}</span>r3-01 Big Sticker (reference)</a>'
    rows = "".join(
        f'<tr id="row-{n:02d}"><td><a href="#logo-{n:02d}">{use(f"f{n}-ok", 40)}<span><b>r4-{n:02d}</b> {html.escape(name)}</span></a></td>'
        f'<td><b>{html.escape(axis)}</b></td><td class="val">{html.escape(ref_value)}</td>'
        f'<td>{html.escape(new_value)}</td></tr>'
        for n, _slug, name, axis, ref_value, new_value, _why, _over in AXES)
    top = "".join(f'<li><span class="rank">{i}</span>{use(f"f{n}-ok", 64)}<div><b>{n:02d} · {html.escape(name)}</b>'
                  f'<span class="why">{html.escape(why)}</span></div></li>' for i, (n, name, why) in enumerate(TOP3, 1))
    crows = "".join(
        f'<tr><td><span class="swatch" style="background:{a}"></span><span class="swatch" style="background:{b}"></span></td>'
        f'<td>{html.escape(label)}</td><td>{ratio:.2f}:1</td><td>{minimum:.1f}:1</td>'
        f'<td class="{"pass" if ratio >= minimum else "fail"}">{"pass" if ratio >= minimum else "FAIL"}</td></tr>'
        for label, ratio, minimum, a, b in contrast_rows())
    checks = (f"all {count} files pass: symbolic files use only rect / circle / path with fills, opacity, transforms and GTK state "
              "classes; full marks use no text, fonts or raster; the face markup is identical to r3-01's in every file; the builder "
              "reproduces r3-01 byte for byte with no override" if not problems else f"PROBLEMS: {html.escape(str(problems))}")
    cards = "".join(card(by_num[n], gtk) for n in sorted(by_num))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RobotDx Logos Round 4</title><style>{r1.CSS}{r2.EXTRA_CSS}{EXTRA_CSS}</style></head>
<body>{sprite()}<main>
<h1>RobotDx - round 4: ten orthogonal variants of r3-01 Big Sticker</h1>
<p class="lede">Zach picked <b>r3-01 Big Sticker</b>: a white head with a calm two-dot face and a big rose
<code>{ROSE}</code> heart sticker tilted over its lower-right corner. Each variant below changes <b>exactly one design axis</b> and
holds everything else at r3-01's value, and no two variants move the same axis - so the winners combine ("r4-03's heart
position + r4-07's tile"). Every card puts r3-01 beside the variant at 256 / 64 / 32 / 22 px, on a light and a dark GNOME top
bar, in the dock, in all four health states and as the real 22 px GTK icon. The face is the same in every mark and state;
health shows only through the heart (full, half, broken, dotted).</p>
<table class="axes"><thead><tr><th>Variant</th><th>Axis</th><th>r3-01's value</th><th>This variant's value</th></tr></thead>
<tbody>{rows}</tbody></table>
<p class="note" style="margin:8px 0 24px">Not used: the suggested 11th axis, sticker peel (a lifted, curled corner). A curl is drawn on the
heart's own outline, so it changes how the half / broken / dotted states look (a second axis), and it overlaps 04's sticker material.
Layout fits: a bigger heart or a thicker rim can reach past the 64 px frame, so the whole composition is shrunk or shifted as little as
needed (uniformly, so no proportion changes); the card says when that happened.</p>
<ul class="rules">
<li>All ten are original geometry - rounded rectangles, superellipses, square pixels and the round-1 heart - with no Disney silhouette or artwork.</li>
<li>In the top bar only the heart takes colour (GTK's <code>.warning</code> / <code>.error</code> classes); the head and face keep the panel's ink, and stale changes shape only.</li>
<li>File checks: {checks}.</li>
</ul>
<section class="top5"><h2>Top 3</h2><ol>{top}</ol></section>
<h2 style="font-size:18px;margin:0">All 10, with the reference</h2>
<div class="overview">{ref}{overview}</div>
<section class="top5"><h2>Contrast</h2><table class="contrast"><thead><tr><th></th><th>Pair</th><th>Ratio</th><th>Floor</th><th></th></tr></thead>
<tbody>{crows}</tbody></table>
<p class="note" style="margin-top:10px">The floors are r3-01's own numbers for the colour pairs the brand fixes (rose on white is 2.43:1, which is why
the sticker has a grey rim and the head a grey outline): no variant introduces a pair worse than r3-01's.</p></section>
{cards}
</main></body></html>
"""


def main():
    check_baseline()
    problems, count = write_files()
    gtk, sheet = gtk_proof()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(gallery(gtk, problems, count))
    print(f"baseline: mark() with no override == r3-01 in all {len(STATES) * 2} full / symbolic states")
    print(f"wrote {count} r4 svg files under {LOGOS}")
    print(f"checks: {'all pass' if not problems else problems}")
    for label, ratio, minimum, *_ in contrast_rows():
        print(f"contrast {ratio:5.2f}:1 (floor {minimum}) {'ok' if ratio >= minimum else 'FAIL'} - {label}")
    print(f"gtk sheet: {sheet}")
    print(f"gallery: {REPORT} ({REPORT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
