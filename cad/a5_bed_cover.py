"""JG Aurora A5 bed cover: 2 x 2 jigsaw tiles that each fit the Bambu A1 mini (180 x 180).

    .venv\\Scripts\\python cad\\a5_bed_cover.py                 305 mm square, 12 mm thick
    .venv\\Scripts\\python cad\\a5_bed_cover.py --size 310 --thick 8

Each tile has one knob and one socket, so the four only go together one way.
The outer corner of each tile has a marker pocket with 1 to 4 ID dimples, the same as
spacer_plate.stl (0 front-left, 1 front-right, 2 back-right, 3 back-left).
Engraved arrows on the two front tiles point to the FRONT of the printer.
Print flat side down. Output: cad/out/a5_cover_<id>_<name>.stl
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import manifold3d as m3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from emcscan import geometry  # noqa: E402
from emcscan.cad import box, cyl, save  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from a5_fixture import GRID, R_ROOT, hole_cutter  # noqa: E402

NECK_W, NECK_L = 8.0, 10.0      # knob neck width, and how far the head center sits past the seam
HEAD_D = 14.0                   # knob head diameter (round)
FIT = 0.25                      # clearance per side between knob and socket
POCKET_DEPTH = 0.4


def knob(clear: float = 0.0) -> m3.CrossSection:
    """Knob in a local frame: seam on x = 0, sticking out toward +x, centered on y = 0."""
    neck = m3.CrossSection.square((NECK_L + 1.0, NECK_W)).translate((-1.0, -NECK_W / 2))
    head = m3.CrossSection.circle(HEAD_D / 2, 64).translate((NECK_L, 0))
    k = neck + head
    return k.offset(clear, m3.JoinType.Round) if clear else k


def placed(clear: float, x: float, y: float, deg: float) -> m3.CrossSection:
    return knob(clear).rotate(deg).translate((x, y))


def grid_holes(size, s, ps, centers, knobs, margin=3.0):
    """Grid symmetric about the seams (20 mm off each seam), minus holes that would hit
    a marker pocket or its ID dimples, a knob or socket, or the outer edge."""
    rr = R_ROOT + margin
    vals = sorted({s + GRID / 2 + GRID * k for k in range(-8, 8)} )
    vals = [v for v in vals if rr <= v <= size - rr]
    out = []
    for hx in vals:
        for hy in vals:
            # marker pocket plus dimple line: keep a circle around each marker center
            if any(math.hypot(hx - cx, hy - cy) < ps / 2 * 1.41 + 3 + 3 * 3.5 + 1.5 + rr
                   for cx, cy in centers.values()):
                continue
            # knob heads and necks (with socket clearance)
            bad = False
            for _o, _i, kx, ky, deg in knobs:
                ux, uy = math.cos(math.radians(deg)), math.sin(math.radians(deg))
                hcx, hcy = kx + ux * NECK_L, ky + uy * NECK_L
                if math.hypot(hx - hcx, hy - hcy) < HEAD_D / 2 + FIT + rr:
                    bad = True
                if math.hypot(hx - kx, hy - ky) < NECK_W / 2 + FIT + rr:
                    bad = True
            if not bad:
                out.append((hx, hy))
    return out


def build(size: float, thick: float, out: Path) -> None:
    s = size / 2
    cfg = {"plate": {"width": size, "depth": size, "height": thick, "marker_size": 20.0,
                     "marker_border": 3.0, "marker_inset": 4.0, "pocket_depth": POCKET_DEPTH}}
    ps = geometry.pocket_size(cfg)
    centers = geometry.marker_centers_body(cfg)
    # tile id -> (name, x0, y0); id matches the marker in its outer corner
    tiles = {0: ("front_left", 0, 0), 1: ("front_right", s, 0),
             2: ("back_right", s, s), 3: ("back_left", 0, s)}
    # knob on the seam, owned by tile 'owner', pushing into tile 'into'
    knobs = [(0, 1, s, s / 2, 0), (1, 2, 1.5 * s, s, 90),
             (2, 3, s, 1.5 * s, 180), (3, 0, s / 2, s, 270)]
    holes = grid_holes(size, s, ps, centers, knobs)
    print(f"  {len(holes)} fixture holes, {GRID:.0f} mm grid")
    out.mkdir(parents=True, exist_ok=True)
    for tid, (name, x0, y0) in tiles.items():
        sec = m3.CrossSection.square((s, s)).translate((x0, y0))
        for owner, into, kx, ky, deg in knobs:
            if owner == tid:
                sec = sec + placed(0.0, kx, ky, deg)
            if into == tid:
                sec = sec - placed(FIT, kx, ky, deg)
        part = m3.Manifold.extrude(sec, thick)
        # marker pocket and ID dimples (toward the cover center)
        cx, cy = centers[tid]
        part = part - box(cx - ps / 2, cy - ps / 2, thick - POCKET_DEPTH, cx + ps / 2, cy + ps / 2, thick + 1)
        ux, uy = s - cx, s - cy
        n = math.hypot(ux, uy); ux, uy = ux / n, uy / n
        for k in range(tid + 1):
            r0 = ps / 2 * 1.41 + 3 + k * 3.5
            part = part - cyl(cx + ux * r0, cy + uy * r0, thick - 0.6, 1, 2.0, 24)
        if y0 == 0:   # front arrow, engraved at the front edge center of the front tiles
            ax = x0 + s / 2
            tri = m3.CrossSection([[(ax, 2.0), (ax + 6, 9.0), (ax - 6, 9.0)]])
            part = part - m3.Manifold.extrude(tri, 1.0).translate((0, 0, thick - 0.6))
        # splined fixture holes on the sparse grid (see a5_fixture.py)
        for hx, hy in holes:
            if x0 <= hx < x0 + s and y0 <= hy < y0 + s:
                part = part - hole_cutter(thick).translate((hx, hy, 0))
        # move to the origin so the slicer sees it in place
        lo = part.bounding_box()
        part = part.translate((-lo[0], -lo[1], 0))
        save(part, out / f"a5_cover_{tid}_{name}.stl")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=float, default=305.0, help="cover width = depth, mm")
    ap.add_argument("--thick", type=float, default=12.0, help="cover thickness, mm")
    a = ap.parse_args()
    build(a.size, a.thick, ROOT / "cad" / "out")
