"""JG Aurora A5 Z endstop trigger spacer, extended so Z homes higher (clears the 50 mm cover).

    .venv\\Scripts\\python cad\\a5_z_spacer.py               60 mm extension
    .venv\\Scripts\\python cad\\a5_z_spacer.py --ext 55 --mirror

The stock white spacer on the Z arm (15 mm tall, 21.2 mm wide, two screws 13 mm apart)
comes down onto the Z switch. This copy keeps the same top (mounting) end and grows
EXT mm downward, so the gantry stops EXT mm higher.

Buried screws: a side window at each screw lets a short hex key (ball end is easiest)
reach the head from the front, so no long bit is needed. A rib between the windows
and the back wall keep it stiff. Bottom: optional M3 fine-adjust screw in a captive nut
(screw head down, it becomes the strike point; lock with a second nut or a dab of glue).

Print standing, mounting face down (as exported). Output: cad/out/a5_z_spacer_ext<EXT>.stl
ASSUMED (confirm): depth 14 mm, M3 + M2 screws, holes centered in depth and across the width,
original screws pass through 12 mm of plastic under their heads.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import manifold3d as m3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from emcscan.cad import box, cyl, save  # noqa: E402

H0, W, D = 15.0, 21.2, 14.0          # stock spacer: height, width, depth (depth ASSUMED)
PITCH = 13.0                         # screw center to center
SEAT = 12.0                          # plastic under each screw head (ASSUMED, as stock)
WIN_H = 24.0                         # window height: room to insert the screw and the key
BACK = 3.5                           # back wall left behind the windows
SCREWS = [("M3", 3.4, 6.2), ("M2", 2.4, 4.4)]   # name, clearance hole, head clearance


def spacer(ext: float, mirror: bool, adjust: bool = True) -> m3.Manifold:
    H = H0 + ext
    part = box(0, 0, 0, W, D, H)
    xs = [W / 2 - PITCH / 2, W / 2 + PITCH / 2]
    if mirror:
        xs = xs[::-1]
    seat_z = H - SEAT
    for (name, hole, head), x in zip(SCREWS, xs):
        part = part - cyl(x, D / 2, seat_z - 1, SEAT + 2, hole, 32)          # through the seat
        hw = head / 2 + 1.2                                                    # window half width
        part = part - box(x - hw, -1, seat_z - WIN_H, x + hw, D - BACK, seat_z)
        part = part - cyl(x, D / 2, seat_z - WIN_H - 0.5, WIN_H + 0.5, head, 32)  # round the back
    if adjust:  # M3 fine-adjust screw from the bottom, captive nut slot open to the front
        part = part - cyl(W / 2, D / 2, -1, 16, 3.4, 32)
        nut = m3.CrossSection.circle(5.8 / math.sqrt(3), 6).rotate(30)
        slot = m3.Manifold.extrude(nut, 2.8).translate((W / 2, D / 2, 6))
        part = part - slot - box(W / 2 - 2.95, -1, 6, W / 2 + 2.95, D / 2, 8.8)
    # flip so the mounting face (top) prints on the bed
    part = part.rotate((180, 0, 0))
    lo = part.bounding_box()
    return part.translate((-lo[0], -lo[1], -lo[2]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ext", type=float, default=60.0)
    ap.add_argument("--mirror", action="store_true", help="swap the M3 and M2 holes")
    ap.add_argument("--no-adjust", action="store_true")
    a = ap.parse_args()
    tag = f"ext{a.ext:g}" + ("_mirror" if a.mirror else "")
    save(spacer(a.ext, a.mirror, not a.no_adjust), ROOT / "cad" / "out" / f"a5_z_spacer_{tag}.stl")
