"""A5 scan fixture kit: splined cover holes, rotating ovals (free and locking), rest pads,
cable tie-down posts. No metal: boards fasten with M3 (or M2.5) nylon screws and nuts.

    .venv\\Scripts\\python cad\\a5_fixture.py          writes cad/out/a5_kit_*.stl

How it works (see vault: Near-Field EMC Scanner Notes > A5 fixture):
  The cover has splined holes on a sparse 40 mm grid. An oval drops into a hole and turns
  on its bottom peg; a nylon screw at the other end (offset r) goes up through a board
  mounting hole. Two ovals hold a board, but two freely turning ovals form a four-bar
  linkage and the board can still swing, so ONE oval is the locking type: its splined peg
  meshes with the hole in 20 degree steps. The other is free (round peg). Rest pads hold up
  the rest of the board. Board hole spacing reachable from two holes D apart: D-2r to D+2r.

Print orientation is already correct (oval top face on the bed, peg pointing up).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import manifold3d as m3

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from emcscan.cad import cyl, save  # noqa: E402

# --- splined hole in the cover (also used by a5_bed_cover.py) ---
N_TEETH = 18            # 20 degree lock steps
R_ROOT = 6.3            # hole outer radius (between ridges)
R_TIP = 5.5             # ridge tip radius
HOLE_DEPTH = 12.0
FIT = 0.2               # clearance per side, peg to hole
GRID = 40.0             # hole pitch on the cover

# --- ovals ---
PEG_LEN = 10.0
BODY_T = 6.0            # board sits this far above the cover (plus any nylon spacer)
END_R = 9.0             # oval pivot end radius (must overhang the 6.3 mm hole)
TIP_R = 4.5             # screw end radius. Tapered (2026-11-18): round 9 mm ends collided, so two
                        # ovals could not bring board holes closer than 18 mm; now 9 mm
NUTS = {"m3": (3.4, 5.5 + 0.3, 2.6), "m25": (2.9, 5.0 + 0.3, 2.2)}   # screw hole, nut AF, nut depth


def spline_section(r_root=R_ROOT, r_tip=R_TIP, n=N_TEETH) -> m3.CrossSection:
    """Star outline: root arcs (outer) alternate with ridge tips (inner), trapezoid flanks."""
    pts = []
    step = 2 * math.pi / n
    for i in range(n):
        a = i * step
        for frac, r in ((0.00, r_root), (0.45, r_root), (0.55, r_tip), (0.90, r_tip)):
            t = a + frac * step
            pts.append((r * math.cos(t), r * math.sin(t)))
    pts.append(pts[0])
    return m3.CrossSection([pts[:-1]])


def hole_cutter(top_z: float) -> m3.Manifold:
    """Splined blind hole, centered on the origin, opening at top_z."""
    return m3.Manifold.extrude(spline_section(), HOLE_DEPTH + 1).translate((0, 0, top_z - HOLE_DEPTH))


def peg(lock: bool) -> m3.Manifold:
    """Peg pointing down from z = 0 to z = -PEG_LEN, with a 1 mm entry chamfer."""
    cs = spline_section().offset(-FIT, m3.JoinType.Miter) if lock else m3.CrossSection.circle(R_TIP - FIT, 64)
    body = m3.Manifold.extrude(cs, PEG_LEN - 1).translate((0, 0, -(PEG_LEN - 1)))
    tip = m3.Manifold.extrude(cs, 1.0, scale_top=(0.82, 0.82)).rotate((180, 0, 0)).translate((0, 0, -(PEG_LEN - 1)))
    return body + tip


def flip_for_print(m: m3.Manifold) -> m3.Manifold:
    m = m.rotate((180, 0, 0))
    lo = m.bounding_box()
    return m.translate((-lo[0], -lo[1], -lo[2]))


def oval(r: float, lock: bool, screw: str = "m3") -> m3.Manifold:
    hole_d, nut_af, nut_d = NUTS[screw]
    ends = m3.CrossSection.circle(END_R, 64) + m3.CrossSection.circle(TIP_R, 48).translate((r, 0))
    body = m3.Manifold.extrude(ends.hull(), BODY_T)
    body = body - cyl(r, 0, -1, BODY_T + 2, hole_d, 32)
    hexa = m3.CrossSection.circle(nut_af / math.sqrt(3), 6).rotate(30).translate((r, 0))
    body = body - m3.Manifold.extrude(hexa, nut_d + 0.01).translate((0, 0, -0.01))
    if lock:  # two dimples on the top face mark the locking type
        for k in (0, 1):
            body = body - cyl(-3 + 3.2 * k, 5.0, BODY_T - 0.6, 1, 1.8, 24)
    return body + peg(lock)


# --- cone pins: press into the oval's screw hole from the top; the board hole rides down
# the cone until it touches, so one pin centers any hole size in its range, no slop ---
SHANK_D, SHANK_L = 3.25, 5.5      # slip fit in the oval's 3.4 mm hole. 3.55 was too big on the
                                  # A1 mini (2026-11-18); the board pushes the pin down, so slip is fine.
                                  # Tune with the shank gauge (a5_fit_shank_gauge.stl)
SHANK_GAUGE = (3.0, 3.1, 3.2, 3.3, 3.4, 3.5)   # tip first, 4 mm per step
CONE_H = 5.0
CONES = {"small": (5.0, 2.2), "large": (7.2, 4.0)}   # base and tip diameter = board hole range


def cone_pin(size: str) -> m3.Manifold:
    """Print orientation: shank on the bed, cone up. 45 degree collar between shank and cone."""
    base, tip = CONES[size]
    collar_h = (base - SHANK_D) / 2
    shank = m3.Manifold.cylinder(SHANK_L, SHANK_D / 2, SHANK_D / 2, 32)
    collar = m3.Manifold.cylinder(collar_h, SHANK_D / 2, base / 2, 48).translate((0, 0, SHANK_L))
    cone = m3.Manifold.cylinder(CONE_H, base / 2, tip / 2, 48).translate((0, 0, SHANK_L + collar_h))
    return shank + collar + cone


def shank_gauge() -> m3.Manifold:
    """Stepped plug gauge for the oval's screw hole: push it in from the top until it stops.
    Count the steps that went in from the tip (1 = 3.0 mm ... 6 = 3.5 mm). Prints upright."""
    g = m3.Manifold.cylinder(8, 4, 4, 48)                    # handle on the bed
    z = 8.0
    for d in reversed(SHANK_GAUGE):
        g += m3.Manifold.cylinder(4, d / 2, d / 2, 48).translate((0, 0, z))
        z += 4
    return g


def board_height(size: str, hole_d: float) -> float:
    """Height of the board's underside above the oval top for a given board hole diameter.
    The collar bottom seats on the oval top face."""
    base, tip = CONES[size]
    collar_h = (base - SHANK_D) / 2
    seat = collar_h                   # collar bottom sits on the oval top (shank is shorter than the body)
    hole_d = min(max(hole_d, tip), base)
    return seat + (base - hole_d) / (base - tip) * CONE_H


def rest_pad(height: float = BODY_T, lock: bool = False) -> m3.Manifold:
    """Free-standing support under the board. Height above the cover = oval body + cone height
    for that board's hole size, so the board sits level (see board_height)."""
    return m3.Manifold.cylinder(height, END_R, END_R, 64) + peg(lock)


def tie_post() -> m3.Manifold:
    """Cable tie-down: 22 mm post with a zip-tie slot, locking peg so it cannot spin."""
    post = m3.Manifold.cylinder(22, 7, 7, 48)
    slot = m3.Manifold.cube((20, 5, 2.4)).translate((-10, -2.5, 14))
    return post - slot + peg(True)


def build_kit(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for r in (10, 15, 20):
        for lock in (False, True):
            save(flip_for_print(oval(r, lock)), out / f"a5_kit_oval_r{r}_{'lock' if lock else 'free'}_m3.stl")
    for size in CONES:
        save(cone_pin(size), out / f"a5_kit_cone_pin_{size}.stl")
    # rest pads matched to common board holes, so the board sits level with the cone pins
    for size, hole in (("small", 2.7), ("small", 3.2), ("small", 3.5), ("large", 4.3)):
        h = BODY_T + board_height(size, hole)
        print(f"    rest pad for {hole} mm holes on the {size} cone: {h:.2f} mm")
        save(flip_for_print(rest_pad(h)), out / f"a5_kit_rest_pad_hole{hole:.1f}_{size}.stl")
    save(flip_for_print(tie_post()), out / "a5_kit_tie_post.stl")
    save(shank_gauge(), out / "a5_fit_shank_gauge.stl")
    # spline fit test: one hole in a small block (print with the free and lock r10 ovals)
    blk = m3.Manifold.cube((26, 26, HOLE_DEPTH + 2)) - hole_cutter(HOLE_DEPTH + 2).translate((13, 13, 0))
    save(blk, out / "a5_fit_spline_block.stl")


if __name__ == "__main__":
    build_kit(HERE / "out")
