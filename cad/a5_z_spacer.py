"""JG Aurora A5 Z endstop trigger spacer, extended so Z homes higher (clears the 50 mm cover).

2026-11-24: default 80 mm (was 60) for probe mount v6, whose tip hangs 20 mm lower; Z must
home 20 mm higher or G28 Z drives the tip into the cover. Z = 0 then puts the tip where it was.
The top of Z travel drops by the same 20 mm: z_max 250 -> 230.

    .venv\\Scripts\\python cad\\a5_z_spacer.py               80 mm extension (mount v6)
    .venv\\Scripts\\python cad\\a5_z_spacer.py --ext 55 --mirror

Stock white spacer (Jacob's calipers, 2026-11-16), mounting face up against the Z carriage,
bottom face strikes the Z switch:
  - footprint W 15 (x) by D 21 (y), block 15.25 tall; a 4.2 mm flange strip runs the full
    21 mm length along one long side (x 0..FLANGE_W), the rest is the full-height block
  - screw A: flange, x 3, y 15 from the flange's outer corner; head sits on the flange underside
  - screw B: block, x 10.8, y 4; 6.5 mm counterbore from the bottom up to flange level
  - a third small hole is unused and is left out
Both screws pass through 4.2 mm of plastic (the flange thickness).

This copy keeps the flange and grows the block EXT mm downward, so the gantry stops EXT mm
higher. Screw A stays open from below and from the sides (nothing under the flange).
Screw B would be buried 70+ mm deep, so a side window opens the block from the step face
(the flange side, where a right-angle hex key comes in) at the screw head. The window is the
stock 6.5 mm counterbore width, so it leaves the same 0.95 mm outer skin the stock part has. Bottom: optional M3 fine-adjust screw in a captive nut (screw head
down, it becomes the strike point; lock with a second nut or a dab of glue).

Print mounting face down (as exported; z = 0 is the mounting face).
Output: cad/out/a5_z_spacer_ext<EXT>.stl
All sizes measured by Jacob 2026-11-16 (caliper A to B 12.2 to 13.5 mm, within reading error).
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

W, D, H0 = 15.0, 21.0, 15.25         # stock: width (x), depth (y), height
FLANGE_T = 4.2                       # flange thickness, also the plastic under both screw heads
FLANGE_W = 5.7                       # flange strip width along x (outer edge to the step)
HOLE_A = (3.0, 15.0)                  # flange screw (x, y)
HOLE_B = (10.8, 4.0)                 # block screw (x, y)
CLEAR = 3.4                          # M3 clearance
HEAD = 6.5                           # stock counterbore, M3 socket head with room
WIN_H = 24.0                         # window height below the head: head + key short leg
WIN_W = HEAD                         # window width (y), same as the stock counterbore


def spacer(ext: float, mirror: bool, adjust: bool = True) -> m3.Manifold:
    H = H0 + ext
    part = box(0, 0, 0, W, D, FLANGE_T) + box(FLANGE_W, 0, 0, W, D, H)
    # both screws through the flange layer
    for x, y in (HOLE_A, HOLE_B):
        part = part - cyl(x, y, -1, FLANGE_T + 2, CLEAR, 32)
    # screw B: window from the step face (x = FLANGE_W) to the head, round back end
    bx, by = HOLE_B
    z0 = FLANGE_T
    part = part - box(FLANGE_W - 1, by - WIN_W / 2, z0, bx, by + WIN_W / 2, z0 + WIN_H)
    part = part - cyl(bx, by, z0, WIN_H, WIN_W, 48)
    if adjust:  # M3 fine-adjust screw from the strike end, captive nut slot open to the step face
        cx, cy = (FLANGE_W + W) / 2, D / 2
        part = part - cyl(cx, cy, H - 16, 17, CLEAR, 32)
        nut = m3.CrossSection.circle(5.8 / math.sqrt(3), 6).rotate(30)
        nz = H - 8.8
        part = part - m3.Manifold.extrude(nut, 2.8).translate((cx, cy, nz))
        part = part - box(FLANGE_W - 1, cy - 2.95, nz, cx, cy + 2.95, nz + 2.8)
    if mirror:  # mirror image: flange on the other long side, same hole spacing
        part = part.mirror((1, 0, 0)).translate((W, 0, 0))
    return part


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ext", type=float, default=80.0)
    ap.add_argument("--mirror", action="store_true", help="mirror-image part (flange on the other long side)")
    ap.add_argument("--no-adjust", action="store_true")
    a = ap.parse_args()
    tag = f"ext{a.ext:g}" + ("_mirror" if a.mirror else "")
    save(spacer(a.ext, a.mirror, not a.no_adjust), ROOT / "cad" / "out" / f"a5_z_spacer_{tag}.stl")
