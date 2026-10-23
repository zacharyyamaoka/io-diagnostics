#!/usr/bin/env python3
"""Build the 10 round-3 RobotDiagnostics logo iterations and their gallery (2026-10-23).

Round 3 iterates on Zach's three round-2 picks (2026-10-23: "do 10 more iterations of 01 03 and 05, lets go with the
Rose color"): r2-01 Cheek Sticker, r2-03 Chest Heart and r2-05 Heart Badge, plus one hybrid of 01 and 05.

Rules carried from round 2:
  - the calm two-dot face (two eyes joined by a straight line) never changes, in any mark or state;
  - health shows through the heart (the life meter: full / half / broken / dotted) or the app's own status badges;
  - an original mark: no Disney silhouette or artwork;
  - the heart is rose #f483a5 (round 2's colour study; build_logos_r2.colour_study prints the table).

Each iteration changes one or two real things against its seed - proportions, the heart's size, position or tilt, how
it breaks the outline, line weight, how the head is cut, cushion vs flat style, the dock tile, legibility at 22 px -
and CHANGES below says what and why.

Writes, for every iteration r3-NN-<slug>:
  brand/logos/r3-NN-<slug>.svg               full-colour mark, 64 grid
  brand/logos/r3-NN-<slug>-symbolic.svg      22 px monochrome top-bar icon (filled rect / circle / path only)
  brand/logos/states/r3-NN-<slug>-<state>.svg and -<state>-symbolic.svg   warn / error / stale (+ unknown for badges)
and the self-contained gallery reports/media/robotdiagnostics-logos-round3-2026-10-23.html.

Geometry, checkers and the meter / badge come from rounds 1 and 2 (build_logos.py, build_logos_r2.py, imported and
left untouched).

Run: python3 /home/bam/io-diagnostics/brand/build_logos_r3.py
"""

from __future__ import annotations

import base64
import html
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import build_logos as r1  # noqa: E402
import build_logos_r2 as r2  # noqa: E402
from build_logos import EDGE, INK, LOGOS, STATES_DIR, arc_pts, face_el, face_knock, fill, path, rect, rpoly_d  # noqa: E402
from build_logos_r2 import (ROSE, STICKER, WHITE, badge, gap_around, gapped_ring, heart_shape, inside, meter,  # noqa: E402
                            piece, poly_d, rr_pts, sticker)

REPORT = pathlib.Path("/home/bam/io-diagnostics/reports/media/robotdiagnostics-logos-round3-2026-10-23.html")
SHOTS = pathlib.Path("/home/bam/io-diagnostics/reports/media/round3")

assert r2.PAL["heart"] == ROSE == "#f483a5"

FLAT = "#dde2e8"                    # the flat (stroke-less) head, cool grey so a white sticker still reads on it
NIGHT_TILE = ("#353c45", "#171a1f")  # the dock tile: neutral charcoal, so white and rose carry the colour


# ---------------------------------------------------------------- geometry added this round

def circle_pts(cx, cy, radius, step=0.25):
    return arc_pts(cx, cy, radius, 0, 360, max(24, int(2 * math.pi * radius / step)))[:-1]


def _run(flags, start):
    """Indices of the cyclic run of True flags that begins at start."""
    out, k, n = [], start, len(flags)
    while flags[k % n] and len(out) < n:
        out.append(k % n)
        k += 1
    return out


def subtract(outer, hole):
    """outer minus hole for two dense closed outlines, as (d, evenodd): a hole wholly inside stays an evenodd pair; a
    hole that crosses the edge once becomes one outline that walks round the bite - the solid-silhouette version of
    round 2's gapped ring (a sticker or badge breaking a FILLED head without overlapping it)."""
    keep = [not inside(p, hole) for p in outer]
    hole_in = [inside(p, outer) for p in hole]
    if all(keep):
        return (poly_d(outer) + poly_d(hole), True) if all(hole_in) else (poly_d(outer), False)
    starts = [k for k in range(len(outer)) if keep[k] and not keep[k - 1]]
    assert len(starts) == 1, "subtract() handles one bite per call"
    run = [outer[k] for k in _run(keep, starts[0])]
    h_starts = [k for k in range(len(hole)) if hole_in[k] and not hole_in[k - 1]]
    assert len(h_starts) == 1
    bite = [hole[k] for k in _run(hole_in, h_starts[0])]
    if math.dist(bite[0], run[-1]) > math.dist(bite[-1], run[-1]):
        bite.reverse()
    return poly_d(run + bite), False


def solid(outline_d_evenodd, face):
    """A filled silhouette with the face knocked out (evenodd): the solid symbolic style."""
    d, _ = outline_d_evenodd
    return path(d + face, ' fill-rule="evenodd"')


def head(x, y, w, h, r, width=2.0):
    return rect(x, y, w, h, r, f' fill="{WHITE}" stroke="{EDGE}" stroke-width="{r1.fmt(width)}"')


def night_tile(uid, x=4, y=4, w=56, h=56, r=14):
    top, bottom = NIGHT_TILE
    return (f'<defs><linearGradient id="{uid}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{top}"/>'
            f'<stop offset="1" stop-color="{bottom}"/></linearGradient>'
            f'<clipPath id="{uid}k">{rect(x, y, w, h, r)}</clipPath></defs>'
            + rect(x, y, w, h, r, f' fill="url(#{uid})" stroke="#4a525c" stroke-width="1"'))


# ---------------------------------------------------------------- the 10 iterations
# Each builder returns the inner SVG for the 64 grid (sym=False) or the 22 grid (sym=True). The face is always the
# same flat two-dot face: face_el(..., "flat") or, cut out of a solid shape, face_knock(..., "flat").

def c01(st, sym):
    """Big Sticker (r2-01): the heart about 20 % bigger and pushed further over the corner."""
    if sym:
        hx, hb, hd, rot = 16.0, 21.75, 4.3, -10
        blocked = gap_around(heart_shape(hx, hb, hd, rot=rot, grow=1.0 + 0.85))
        ring = piece(gapped_ring(rr_pts(1.6, 3.3, 16.9, 13.7, 4.9), 1.75, blocked))
        return ring + face_el(9.9, 9.6, 4.3, 2.05, 1.45, "flat") + meter(hx, hb, hd, st, 1.2, True, rot=rot)
    hx, hb, hd, rot = 47, 59.5, 8.9, -10
    return (head(4, 7, 50, 42, 16) + face_el(28.5, 26.5, 12.5, 5.4, 3.3, "flat", fill(INK))
            + sticker(hx, hb, hd, 2.6, rot=rot) + meter(hx, hb, hd, st, 2.5, False, rot=rot))


def c02(st, sym):
    """Solid Cheek (r2-01): flat style - a filled grey head with no outline; the symbolic is a solid head with the face
    knocked out and a bite for the heart."""
    if sym:
        hx, hb, hd, rot = 16.3, 21.6, 3.95, -10
        bite = subtract(rr_pts(1.3, 3.4, 18.4, 14.6, 5.2), heart_shape(hx, hb, hd, rot=rot, grow=1.3))
        face = face_knock(10.0, 9.9, 4.5, 2.15, 1.55, "flat")
        return solid(bite, face) + meter(hx, hb, hd, st, 1.15, True, rot=rot)
    hx, hb, hd, rot = 47, 57.5, 7.6, -10
    return (rect(5, 9, 54, 44, 17, fill(FLAT)) + face_el(31, 29, 13, 5.6, 3.4, "flat", fill(INK))
            + sticker(hx, hb, hd, 2.6, rot=rot) + meter(hx, hb, hd, st, 2.3, False, rot=rot))


def c03(st, sym):
    """Wide Cushion (r2-01): a wider, lower head on a thinner line, and the heart stands upright."""
    if sym:
        hx, hb, hd = 16.4, 21.7, 3.85
        blocked = gap_around(heart_shape(hx, hb, hd, grow=1.0 + 0.75))
        ring = piece(gapped_ring(rr_pts(0.95, 5.0, 19.6, 12.2, 4.4), 1.45, blocked))
        return ring + face_el(9.6, 11.0, 5.0, 1.95, 1.3, "flat") + meter(hx, hb, hd, st, 1.05, True)
    hx, hb, hd = 50, 59, 7.6
    return (head(3, 14, 58, 37, 15, 1.4) + face_el(29, 31.5, 14.5, 5.2, 3.0, "flat", fill(INK))
            + sticker(hx, hb, hd, 2.4) + meter(hx, hb, hd, st, 2.2, False))


def c04(st, sym):
    """Big Chest Heart (r2-03): the heart about 25 % bigger, a smaller head so the chest has room for it."""
    if sym:
        hx, hb, hd = 11, 21.75, 3.95
        head_ring = path(r1.rr_ring_d(4.6, 0.4, 12.8, 8.6, 3.9, 1.35), ' fill-rule="evenodd"')
        arch = piece(gapped_ring(rr_pts(1.5, 10.6, 19, 14, 6.2), 1.5, lambda p: p[1] > 21.3))
        return head_ring + face_el(11, 4.7, 3.2, 1.45, 1.05, "flat") + arch + meter(hx, hb, hd, st, 1.15, True)
    hx, hb, hd = 32, 57, 8.3
    body = path(rpoly_d(r1.box(4, 31, 56, 29), [20, 20, 6, 6]), f' fill="{WHITE}" stroke="{EDGE}" stroke-width="2"')
    return (body + head(16, 4, 32, 23, 11.5) + face_el(32, 15.5, 9, 4.0, 2.5, "flat", fill(INK))
            + sticker(hx, hb, hd, 2.2) + meter(hx, hb, hd, st, 2.4, False))


def c05(st, sym):
    """Square Shoulders (r2-03): a boxier torso (tight shoulder corners) and a wider neck gap."""
    if sym:
        hx, hb, hd = 11, 21.3, 3.3
        head_ring = path(r1.rr_ring_d(4, 0.4, 14, 9.3, 4.2, 1.45), ' fill-rule="evenodd"')
        arch = piece(gapped_ring(rr_pts(2.0, 11.7, 18, 13, 3.2), 1.5, lambda p: p[1] > 21.3))
        return head_ring + face_el(11, 5.05, 3.5, 1.55, 1.15, "flat") + arch + meter(hx, hb, hd, st, 1.05, True)
    hx, hb, hd = 32, 55.5, 6.6
    body = path(rpoly_d(r1.box(6, 34, 52, 26), [10, 10, 5, 5]), f' fill="{WHITE}" stroke="{EDGE}" stroke-width="2"')
    return (body + head(14, 4, 36, 25, 12.5) + face_el(32, 16.5, 9.5, 4.2, 2.6, "flat", fill(INK))
            + sticker(hx, hb, hd, 2.2) + meter(hx, hb, hd, st, 2.1, False))


def c06(st, sym):
    """Chest Tile (r2-03): the bust rises from the bottom of a charcoal dock tile; the symbolic's head is solid with
    the face cut out, the shoulders stay an outline."""
    if sym:
        hx, hb, hd = 11, 21.3, 3.3
        head_solid = solid((r1.rr_d(4, 0.4, 14, 9.6, 4.3), False), face_knock(11, 5.2, 3.5, 1.6, 1.2, "flat"))
        # WHY the shoulders stay an outline: a solid chest (tried first) put the filled OK heart inside a dark moat
        # inside a filled shape, which reads as an EMPTY heart outline - the meter's 'full' must look full.
        arch = piece(gapped_ring(rr_pts(1.75, 12.2, 18.5, 13, 6), 1.5, lambda p: p[1] > 21.3))
        return head_solid + arch + meter(hx, hb, hd, st, 1.05, True)
    uid = f"r3t6{st}"
    return (night_tile(uid) + f'<g clip-path="url(#{uid}k)">'
            + path(rpoly_d(r1.box(10, 38, 44, 30), [18, 18, 0, 0]), fill(WHITE)) + "</g>"
            + rect(16, 10, 32, 24, 11.5, fill(WHITE)) + face_el(32, 22, 8.8, 3.9, 2.4, "flat", fill(INK))
            + meter(32, 58, 5.6, st, 1.9, False, tip=0.8))


def c07(st, sym):
    """Big Badge (r2-05): the badge about 20 % bigger and the head a touch smaller, so the 22 px badge carries its glyph."""
    if sym:
        bx, by, radius = 16.55, 5.45, 5.3
        ring = piece(gapped_ring(rr_pts(1.6, 7.4, 14.6, 13.0, 4.0), 1.65, gap_around((bx, by, radius), 1.0 + 0.82)))
        return ring + face_el(8.9, 13.9, 3.75, 1.75, 1.25, "flat") + badge(bx, by, radius, st, True)
    return (head(4, 18, 45, 41, 15.5) + face_el(26.5, 38.5, 11, 4.8, 2.9, "flat", fill(INK))
            + badge(47, 17, 13.4, st, False, backing=2.6))


def c08(st, sym):
    """Notch Badge (r2-05): the head is bitten round the badge instead of the badge sitting on a white sticker over it."""
    if sym:
        bx, by, radius = 16.9, 5.1, 4.85
        ring = piece(gapped_ring(rr_pts(1.75, 6.5, 15.0, 13.8, 4.1), 1.7, gap_around((bx, by, radius), 1.25 + 0.85)))
        return ring + face_el(9.25, 13.4, 3.85, 1.8, 1.3, "flat") + badge(bx, by, radius, st, True)
    bx, by, radius = 48.5, 15.5, 11.2
    bitten, _ = subtract(rr_pts(4, 15, 48, 42, 16), circle_pts(bx, by, radius + 3.6))
    return (path(bitten, f' fill="{WHITE}" stroke="{EDGE}" stroke-width="2" stroke-linejoin="round"')
            + face_el(28, 36, 11.5, 5, 3, "flat", fill(INK)) + badge(bx, by, radius, st, False))


def c09(st, sym):
    """Tile Badge (r2-05): the cushion on a charcoal dock tile, the badge on the tile's corner like an app badge; the
    symbolic is the solid style."""
    if sym:
        bx, by, radius = 17.15, 4.85, 4.6
        bite = subtract(rr_pts(1.3, 6.3, 15.7, 14.3, 4.4), circle_pts(bx, by, radius + 1.05))
        return solid(bite, face_knock(9.15, 13.45, 3.85, 1.85, 1.35, "flat")) + badge(bx, by, radius, st, True)
    uid = f"r3t9{st}"
    return (night_tile(uid, 4, 10, 50, 50, 13) + rect(11, 26.5, 36, 27, 11, ' fill="#000000" opacity="0.28"')
            + rect(11, 24, 36, 27, 11, fill(WHITE)) + face_el(29, 37.5, 9.5, 4.2, 2.6, "flat", fill(INK))
            + badge(50.5, 13.5, 10.2, st, False, backing=2.4))


def c10(st, sym):
    """Sticker Badge (r2-01 + r2-05): Cheek Sticker's head and corner, with Heart Badge's states - the cheek heart is
    replaced, in the same spot, by the app's status shape."""
    if sym:
        bx, by, radius = 16.75, 16.75, 4.75
        ring = piece(gapped_ring(rr_pts(1.9, 3.9, 18.2, 14.2, 5.1), 1.8, gap_around((bx, by, radius), 0.95 + 0.9)))
        return ring + face_el(10.4, 9.8, 4.6, 2.1, 1.5, "flat") + badge(bx, by, radius, st, True)
    return (head(5, 9, 54, 44, 17) + face_el(31, 29, 13, 5.6, 3.4, "flat", fill(INK))
            + badge(49, 50.5, 10.6, st, False, backing=2.6))


# num, slug, name, idea, health (a = heart meter, b = status badge), seed (round-2 number), changes [(what, why)], builder
VARIANTS = [
    (1, "big-sticker", "Big Sticker", "Cheek Sticker with a bigger heart, pushed further over the corner.", "a", 1,
     [("Heart about 20 % bigger (half-diagonal 7.4 -> 8.9 on the 64 grid, 3.75 -> 4.3 at 22 px) and pushed further "
       "out, so more of it hangs outside the head.",
       "At 22 px the seed's heart was 9 px wide and its half, broken and dotted states were the hardest part of the "
       "icon to read; at 10.4 px the split and the dots each get a whole pixel more."),
      ("Head 4 px narrower to make room.", "Keeps the heart from crowding the right eye.")], c01),
    (2, "solid-cheek", "Solid Cheek", "Cheek Sticker in the flat style: a filled grey head with no outline, and a "
     "solid top-bar icon.", "a", 1,
     [("Flat style: the head is a solid cool grey with no outline, instead of a white cushion with a grey rim.",
       "Reads as a flat app glyph rather than a soft toy; the white sticker rim now has something to contrast "
       "with, so the heart looks stuck on."),
      ("Symbolic is a solid silhouette: the head is filled, the face is cut out of it, and the heart sits in a bite.",
       "A solid shape is the heaviest thing in a 22 px top bar - it holds up next to the bold 'IO 3 ⚠' text where "
       "the 1.8 px outline looks thin.")], c02),
    (3, "wide-cushion", "Wide Cushion", "Cheek Sticker made wider and lower, on a thinner line, with the heart "
     "upright.", "a", 1,
     [("Proportions 54x44 -> 58x37 and a thinner rim (2 -> 1.4 px; 1.8 -> 1.45 px at 22 px).",
       "A wider head fills the top bar's wide-and-short slot better, and leaves the heart a taller corner to sit in."),
      ("The heart stands upright instead of tilted -10 degrees.", "An upright heart reads as a life meter (a HUD "
       "element) more than a decoration, and its straight sides land on the pixel grid at 22 px.")], c03),
    (4, "big-chest-heart", "Big Chest Heart", "Chest Heart with a much bigger heart and a smaller head.", "a", 3,
     [("Heart about 25 % bigger (half-diagonal 6.6 -> 8.3; 3.3 -> 3.95 at 22 px).",
       "The seed's chest heart was 8 px wide in the top bar - the smallest meter of the three seeds. The meter is "
       "the point, so it gets the biggest share of the icon."),
      ("Head 2 px narrower and shorter; at 22 px the head is smaller and the chest arch starts 1.6 px higher.",
       "Buys the heart the room without the head touching it.")],
     c04),
    (5, "square-shoulders", "Square Shoulders", "Chest Heart with a boxier torso and a wider neck gap.", "a", 3,
     [("How the body is cut: tight shoulder corners (radius 19 -> 10 on the 64 grid, 6 -> 3.2 at 22 px), a "
       "boxy robot torso instead of a rounded bust.",
       "Reads as a robot rather than a soft character, and moves the mark further from any rounded-body cartoon "
       "silhouette. (A version with the head sitting down over a rounder body was tried first and dropped: it read "
       "as exactly that silhouette.)"),
      ("Neck gap 4 -> 5 px (1.05 -> 1.25 px at 22 px).", "The gap between head and torso is what separates the two "
       "shapes in the top bar; a quarter pixel more keeps it a clean dark line after anti-aliasing.")], c05),
    (6, "chest-tile", "Chest Tile", "Chest Heart as a dock tile: the bust rises out of the bottom of a charcoal "
     "tile; a solid head in the top bar.", "a", 3,
     [("Dock tile: a charcoal rounded tile with the white bust cropped by its bottom edge, like a profile avatar.",
       "In the dock the seed was a floating white shape that disappears on a light dock; a tile gives it the same "
       "footprint as every other app icon and makes the rose heart pop."),
      ("Symbolic head is solid with the face cut out; the shoulders stay an outline.",
       "The seed's two thin outlines (head and arch) were the busiest 22 px icon in round 2; one solid shape anchors "
       "it. Solid shoulders were tried too and dropped - the filled OK heart inside them read as an empty outline.")],
     c06),
    (7, "big-badge", "Big Badge", "Heart Badge with a bigger badge and a slightly smaller head.", "b", 5,
     [("Badge about 20 % bigger (radius 11.2 -> 13.4; 4.5 -> 5.3 at 22 px).",
       "At 22 px the seed's octagon cross and the ? were 1-2 px glyphs; at 10.6 px across they survive the "
       "real GTK render."),
      ("Head 3 px smaller and lower.", "Keeps the face clear of the bigger badge.")], c07),
    (8, "notch-badge", "Notch Badge", "Heart Badge with the head bitten round the badge instead of the badge "
     "sitting on a white sticker.", "b", 5,
     [("How the badge breaks the outline: the head is cut away round the badge (a notch) instead of a white sticker "
       "covering it.",
       "The full-colour mark now matches the top-bar icon, which already has to gap the outline; the badge reads as "
       "the app's own status badge, not a sticker, and the white disc no longer merges with the white head."),
      ("Symbolic gap 1.9 -> 2.1 px and the badge 8 % bigger.", "A wider notch stays a visible gap after GTK's "
       "anti-aliasing.")], c08),
    (9, "tile-badge", "Tile Badge", "Heart Badge as a dock tile: the cushion on a charcoal tile, the badge on the "
     "tile's corner; solid top-bar icon.", "b", 5,
     [("Dock tile: the white cushion sits on a charcoal rounded tile and the badge overhangs the tile's top-right "
       "corner, as app badges do.",
       "Looks like an app in the dock and keeps the badge where people already look for notification counts."),
      ("Symbolic is solid: a filled cushion with the face cut out, bitten round the badge.",
       "Heavier and clearer in the top bar than the outline.")], c09),
    (10, "sticker-badge", "Sticker Badge", "Hybrid: Cheek Sticker's head and corner with Heart Badge's states - the "
     "cheek heart is swapped for the app's status shape in the same spot.", "b", "1+5",
     [("Takes 01's lower-right sticker position and 05's status shapes.",
       "The badge leaves the top of the head clean (no fight with the top bar's centre line), the heart stays where "
       "01 put it, and every problem is the exact shape the app already uses."),
      ("The badge sits on a round white sticker on the cheek.", "OK and every problem state share one footprint, so "
       "the mark's outline never changes - only what is on the sticker.")], c10),
]

SEEDS = {e[0]: e for e in r2.VARIANTS if e[0] in (1, 3, 5)}

TOP3 = [
    (1, "Big Sticker", "The seed Zach liked most, made more legible: the 22 px heart is 10.4 px wide instead of 9, so "
        "half, broken and dotted are the clearest life-meter states in the set (see the GTK row), and at 64-256 px the "
        "bigger sticker hanging off the corner is more playful without touching the face."),
    (10, "Sticker Badge", "The best of both seeds: 01's look (calm cushion, heart sticker on the cheek) when OK, and "
        "05's exact app status shapes when not, in one fixed spot - so the outline never changes and a problem is "
        "the same triangle or octagon the app shows. The badge sits low, clear of the top bar's centre line."),
    (9, "Tile Badge", "The strongest dock tile and the boldest top-bar icon: a white cushion on a charcoal tile holds up "
        "on light and dark docks, the badge sits where app badges live, and the solid 22 px silhouette is the "
        "heaviest shape beside 'IO 3 ⚠'."),
]
RUNNER_UP = ("Runner-up: 04 Big Chest Heart - the best of the Chest Heart line; its 9.5 px chest heart is the "
             "biggest meter of the three bust variants, but the bust still spends half the 22 px icon on shoulders.")


def states_of(entry):
    return r2.BADGE_STATES if entry[4] == "b" else r2.METER_STATES


def seeds_of(entry):
    return [int(s) for s in str(entry[5]).split("+")]


# ---------------------------------------------------------------- files

def stem_of(entry):
    return f"r3-{entry[0]:02d}-{entry[1]}"


def write_files():
    LOGOS.mkdir(parents=True, exist_ok=True)
    STATES_DIR.mkdir(parents=True, exist_ok=True)
    problems, count = {}, 0
    for entry in VARIANTS:
        num, slug, name, *_rest, build = entry
        stem = stem_of(entry)
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


def sym_file(stem, state):
    return LOGOS / f"{stem}-symbolic.svg" if state == "ok" else STATES_DIR / f"{stem}-{state}-symbolic.svg"


def rows_for_gtk():
    """(key, stem, states) for every icon the gallery proves: the 10 iterations, then the three round-2 seeds."""
    rows = [(entry[0], stem_of(entry), states_of(entry)) for entry in VARIANTS]
    rows += [(f"seed{num}", f"r2-{num:02d}-{seed[1]}", r2.states_of(seed)) for num, seed in SEEDS.items()]
    return rows


def gtk_proof():
    """Each symbolic file (the seeds' too) through Gtk.IconInfo.load_symbolic at 22 px, as rounds 1 and 2 did."""
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
    rows = rows_for_gtk()
    sheet = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, cell * 10 + 12, cell * len(rows) + 12)
    sheet.fill(0x808080ff)
    for row, (key, stem, states) in enumerate(rows):
        for col, (theme_name, (fg, warn, err, bg)) in enumerate(themes.items()):
            for k, state in enumerate(states):
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
                tile.copy_area(0, 0, 22 * scale, 22 * scale, sheet, 12 + (col * 5 + k) * cell, 12 + row * cell)
    SHOTS.mkdir(parents=True, exist_ok=True)
    sheet_path = SHOTS / "logos-r3-gtk-symbolic-sheet.png"
    sheet.savev(str(sheet_path), "png", [], [])
    return out, sheet_path


# ---------------------------------------------------------------- gallery

def sprite():
    symbols = []

    def add(key, build, states):
        for state in states:
            symbols.append(f'<symbol id="f{key}-{state}" viewBox="0 0 64 64">{build(state, False)}</symbol>')
            symbols.append(f'<symbol id="s{key}-{state}" viewBox="0 0 22 22"><g fill="currentColor">'
                           f'{r2.sym_symbol(build(state, True))}</g></symbol>')
    for entry in VARIANTS:
        add(entry[0], entry[-1], states_of(entry))
    for num, seed in SEEDS.items():
        add(f"seed{num}", seed[-1], r2.states_of(seed))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0" style="position:absolute" aria-hidden="true">'
            + "".join(symbols) + "</svg>")


use = r1.use


def status_set(entry, gtk):
    num = entry[0]
    states = states_of(entry)
    full_l = "".join(f'<figure>{use(f"f{num}-{s}", 64)}<figcaption>{s}</figcaption></figure>' for s in states)
    bar = "".join(f'<span class="chip">{use(f"s{num}-{s}", 22, "tb-ic")}<span>{r2.LABELS[s]}</span></span>' for s in states)

    def proof(theme):
        return "".join(f'<img src="data:image/png;base64,{gtk[(num, s, theme)]}" width="22" height="22" alt="{s}">'
                       f'<img class="zoom" src="data:image/png;base64,{gtk[(num, s, theme)]}" width="66" height="66" alt="">'
                       for s in states)
    return (f'<div class="states"><div class="ladder light st">{full_l}</div><div class="ladder dark st">{full_l}</div></div>'
            f'<div class="topbar dark wide"><span class="tb-left">{" · ".join(states)}</span><span class="tb-right">{bar}</span></div>'
            f'<div class="gtk"><div class="gtk-row dark">{proof("dark")}</div><div class="gtk-row light">{proof("light")}</div>'
            '<p class="note">Real GTK 3.24 <code>Gtk.IconInfo.load_symbolic</code> output of the shipped <code>-symbolic.svg</code> '
            'files at 22 px (left of each pair) and the same pixels at 3x, nearest-neighbour (right).</p></div>')


def vs_column(key, label, gtk, states):
    sizes = "".join(f'<figure>{use(f"f{key}-ok", n)}<figcaption>{n}</figcaption></figure>' for n in (16, 22, 32, 64))
    shown = [s for s in states if s in ("ok", "warn", "error")]
    proof = "".join(f'<figure><img class="zoom" src="data:image/png;base64,{gtk[(key, s, "dark")]}" width="66" height="66" '
                    f'alt="{s}"><figcaption>{s}</figcaption></figure>' for s in shown)
    return (f'<div class="vs-col"><h4>{label}</h4><div class="ladder light">{sizes}</div><div class="ladder dark">{sizes}</div>'
            f'<div class="gtk-row dark vs-gtk">{proof}</div>'
            f'<div class="docks">{r1.dock(key, "dark")}{r1.dock(key, "light")}</div></div>')


def versus(entry, gtk):
    cols = []
    for num in seeds_of(entry):
        seed = SEEDS[num]
        cols.append(vs_column(f"seed{num}", f"Seed · r2-{num:02d} {html.escape(seed[2])}", gtk, r2.states_of(seed)))
    cols.append(vs_column(entry[0], f"Round 3 · r3-{entry[0]:02d} {html.escape(entry[2])}", gtk, states_of(entry)))
    return f'<div class="vs n{len(cols)}">{"".join(cols)}</div>'


def card(entry, gtk):
    num, slug, name, idea, health, seed, changes, _build = entry
    stem = stem_of(entry)
    seed_names = " + ".join(f"r2-{n:02d} {SEEDS[n][2]}" for n in seeds_of(entry))
    changed = "".join(f"<li><b>{html.escape(what)}</b> <span>{html.escape(why)}</span></li>" for what, why in changes)
    files = (f"<code>brand/logos/{stem}.svg</code> · <code>brand/logos/{stem}-symbolic.svg</code> · "
             f"states <code>brand/logos/states/{stem}-{{{','.join(states_of(entry)[1:])}}}[-symbolic].svg</code>")
    return f"""
<section class="card" id="logo-{num:02d}">
  <header>
    <div class="num">{num:02d}</div>
    <div class="title"><h2>{html.escape(name)}</h2><p class="idea">{html.escape(idea)}</p></div>
    <div class="meta"><span class="family">{r2.HEALTH[health]}</span><span class="fit">from {html.escape(seed_names)}</span></div>
  </header>
  <ul class="changed">{changed}</ul>
  <div class="grid">
    <div class="block"><h3>Size ladder - 16 · 22 · 32 · 64 · 256, plus the 22 px symbolic</h3>{r1.ladder(num)}</div>
    <div class="block"><h3>Top bar - beside the chip</h3>{r1.topbar(num, "warn", "IO 3 ⚠")}{r1.topbar(num, "ok", "IO ✓")}{r1.topbar(num, "warn", "IO 3 ⚠", "light")}
      <h3>Dock tile</h3><div class="docks">{r1.dock(num, "dark")}{r1.dock(num, "light")}</div></div>
    <div class="block wide"><h3>Health states - the face never changes</h3>{status_set(entry, gtk)}</div>
    <div class="block wide"><h3>Side by side with its seed - 16 · 22 · 32 · 64, the real 22 px GTK icon (ok · warn · error), the dock</h3>{versus(entry, gtk)}</div>
  </div>
  <p class="files">{files}</p>
</section>"""


EXTRA_CSS = """
.changed{margin:10px 0 14px;padding-left:18px;font-size:13.5px;color:var(--muted)}
.changed li{margin:0 0 4px}
.changed b{color:var(--text);font-weight:600}
.vs{display:grid;gap:12px}
.vs.n2{grid-template-columns:1fr 1fr}
.vs.n3{grid-template-columns:1fr 1fr 1fr}
.vs-col{min-width:0;border:1px solid var(--border);border-radius:10px;padding:10px}
.vs-col:last-child{border-color:var(--accent)}
.vs-col h4{margin:0 0 8px;font-size:13px}
.vs-col .ladder{gap:10px;padding:10px}
.vs-col .ladder + .ladder{margin-top:6px}
.vs-col .docks{margin-top:6px}
.vs-col .dock{gap:6px;padding:6px 8px}
.vs-gtk{margin-top:6px}
.vs-gtk figure{margin:0;display:flex;flex-direction:column;align-items:center;gap:2px}
.vs-gtk figcaption{font-size:10.5px;color:#8a929b;font-family:ui-monospace,monospace}
.top5 .seed{font-weight:400;color:var(--faint);font-size:12.5px}
@media (max-width:820px){.vs.n2,.vs.n3{grid-template-columns:1fr}}
"""


def gallery(gtk, problems, count):
    by_num = {entry[0]: entry for entry in VARIANTS}
    overview = "".join(f'<a href="#logo-{n:02d}"><span class="pair">{use(f"f{n}-ok", 48)}{use(f"s{n}-ok", 22, "symb")}</span>'
                       f'{n:02d} {html.escape(name)}</a>' for n, _slug, name, *_ in VARIANTS)
    seeds = "".join(f'<a><span class="pair">{use(f"fseed{n}-ok", 48)}{use(f"sseed{n}-ok", 22, "symb")}</span>'
                    f'r2-{n:02d} {html.escape(seed[2])}</a>' for n, seed in SEEDS.items())
    top = "".join(f'<li><span class="rank">{i}</span>{use(f"f{n}-ok", 64)}<div><b>{n:02d} · {html.escape(name)} '
                  f'<span class="seed">from {html.escape(" + ".join(f"r2-{k:02d}" for k in seeds_of(by_num[n])))}</span></b>'
                  f'<span class="why">{html.escape(why)}</span></div></li>' for i, (n, name, why) in enumerate(TOP3, 1))
    checks = (f"all {count} files pass: symbolic files use only rect / circle / path with fills and GTK state classes; "
              "full marks use no text, fonts or raster" if not problems else f"PROBLEMS: {html.escape(str(problems))}")
    cards = "".join(card(by_num[n], gtk) for n in sorted(by_num))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RobotDiagnostics Logos Round 3</title><style>{r1.CSS}{r2.EXTRA_CSS}{EXTRA_CSS}</style></head>
<body>{sprite()}<main>
<h1>RobotDiagnostics - round 3: iterating 01, 03 and 05 in rose</h1>
<p class="lede">Ten iterations on Zach's three round-2 picks - <b>01 Cheek Sticker</b> (three iterations), <b>03 Chest
Heart</b> (three) and <b>05 Heart Badge</b> (three) - plus one hybrid of 01 and 05. The heart is rose <code>{ROSE}</code>
throughout. Each iteration changes one or two real things against its seed, listed on its card with the reason, and
sits beside its seed at the same sizes. The face is the same calm two-dot face in every mark and every state; health
shows only through the heart (full, half, broken, dotted) or the app's own status badges (heart, triangle, octagon with
a cross, hollow clock, disc with a question mark).</p>
<ul class="rules">
<li>All ten are original geometry - circles, rounded rectangles and the round-1 heart - with no Disney silhouette or
artwork, no oval head on a pear body and no inflated-vinyl shading.</li>
<li>In the top bar only the heart or the badge takes colour (GTK's <code>.warning</code> / <code>.error</code> classes); the head
and face keep the panel's ink, and stale changes shape only.</li>
<li>File checks: {checks}.</li>
</ul>
<section class="top5"><h2>Top 3</h2><ol>{top}</ol><p class="note" style="margin-top:12px">{html.escape(RUNNER_UP)}</p></section>
<h2 style="font-size:18px;margin:0">All 10</h2>
<div class="overview">{overview}</div>
<h2 style="font-size:14px;margin:0;color:var(--faint)">The round-2 seeds</h2>
<div class="overview">{seeds}</div>
{cards}
</main></body></html>
"""


def main():
    problems, count = write_files()
    gtk, sheet = gtk_proof()
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(gallery(gtk, problems, count))
    print(f"wrote {count} r3 svg files under {LOGOS}")
    print(f"checks: {'all pass' if not problems else problems}")
    print(f"gtk sheet: {sheet}")
    print(f"gallery: {REPORT} ({REPORT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
