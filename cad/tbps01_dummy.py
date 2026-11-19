"""Printed stand-ins for the TekBox TBPS01 probes: crash-test sticks for the A5 mount and
the height-following path. Make the first mistakes with a PLA stick, not a $65 probe.

    .venv\\Scripts\\python cad\\tbps01_dummy.py                  H20 and H10
    .venv\\Scripts\\python cad\\tbps01_dummy.py --probe H10 --flange-t 3.4

What is copied from the real probe (calipers, Jacob, 2026-11-17), because these are the
parts that touch the mount or the board:
    blade 9.25 x 2.0 mm; flat flange 16.5 mm at the bottom of the handle; flange
    underside to tip 84.75 mm; ring outside width and height from emcscan.heightmap.PROBE_TIPS
    (the same numbers the height map uses, so the stick and the software agree).
What is made up: the ring band width, the flange thickness (--flange-t), and a short
14 mm handle stub to hold (the real handle is longer and ribbed).

Print upside down as exported: handle stub on the bed, flange, blade, ring on top. The
45 degree cone under the flange (as printed) keeps it support-free and leaves the flange
face that sits on the mount seat flat. PLA on purpose: if the path is wrong, the stick
snaps instead of the board or the mount. 0.2 mm layers, 3 walls (the blade is solid),
brim on, slow outer walls; about 4 g each.
Output: cad/out/tbps01_dummy_<probe>.stl
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import manifold3d as m3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from emcscan.cad import box, cyl, save  # noqa: E402
from emcscan.heightmap import BLADE_T, PROBE_TIPS  # noqa: E402

BLADE_W = 9.25
BLADE_LEN = 84.75            # flange underside to tip
FLANGE_D = 16.5
STUB_D, STUB_L = 14.0, 25.0
BAND = {"H20": 2.5, "H10": 2.0}


def ring_y(outer_d: float, band: float, t: float) -> m3.Manifold:
    """Ring in the XZ plane (thickness along Y), bottom touching z = 0."""
    R = outer_d / 2
    outer = m3.CrossSection.circle(R, 128)
    inner = m3.CrossSection.circle(R - band, 128)
    ring2d = (outer - inner).translate((0, R))
    # extrude along +Z, then turn so the ring stands in XZ with thickness along Y
    return m3.Manifold.extrude(ring2d, t).translate((0, 0, -t / 2)).rotate((90, 0, 0))


def dummy(name: str, flange_t: float) -> m3.Manifold:
    spec = PROBE_TIPS[name]
    t = BLADE_T
    if spec["ring"]:
        tip = ring_y(spec["tip_w"], BAND.get(name, 2.0), t)
        z_blade = spec["tip_h"] - BAND.get(name, 2.0)
        # 45 degree gussets where the ring shoulders meet the blade: printed upside down
        # they would overhang at about 67 degrees. They sit ABOVE the ring's lowest edge
        # at every X, so they do not change what the height map has to clear.
        R = spec["tip_w"] / 2
        ax, az = R * 0.7071, R + R * 0.7071
        if ax > BLADE_W / 2:
            for s in (-1, 1):
                pts = [(s * x, y, z) for y in (-t / 2, t / 2)
                       for (x, z) in ((ax, az), (BLADE_W / 2, az + ax - BLADE_W / 2), (BLADE_W / 2, az))]
                tip = tip + m3.Manifold.hull_points(pts)
    else:
        tip = None
        z_blade = 0.0
    blade = box(-BLADE_W / 2, -t / 2, z_blade, BLADE_W / 2, t / 2, BLADE_LEN + 0.5)
    part = blade if tip is None else blade + tip
    flange = cyl(0, 0, BLADE_LEN, flange_t, FLANGE_D, 96)
    cone = m3.Manifold.cylinder((FLANGE_D - STUB_D) / 2, FLANGE_D / 2, STUB_D / 2, 96) \
        .translate((0, 0, BLADE_LEN + flange_t))
    stub = cyl(0, 0, BLADE_LEN + flange_t, STUB_L, STUB_D, 96)
    part = part + flange + cone + stub
    # print upside down: stub end on the bed
    part = part.rotate((180, 0, 0))
    lo = part.bounding_box()
    return part.translate((0, 0, -lo[2]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", nargs="*", default=["H20", "H10"], choices=list(PROBE_TIPS))
    ap.add_argument("--flange-t", type=float, default=3.0, help="flange thickness (not measured yet)")
    a = ap.parse_args()
    for name in a.probe:
        p = dummy(name, a.flange_t)
        save(p, ROOT / "cad" / "out" / f"tbps01_dummy_{name}.stl")
        spec = PROBE_TIPS[name]
        print(f"    {name}: ring {spec['tip_w']} wide x {spec['tip_h']} tall, "
              f"volume {p.volume() / 1000:.1f} cm3")
