"""Fit-test coupons for the A5 bed cover jigsaw joint (print before the full tiles).

    .venv\\Scripts\\python cad\\a5_fit_test.py

a5_fit_knob.stl       one knob on a small block (same shape as the tiles)
a5_fit_socket.stl     three sockets in one strip: 0.15, 0.25, 0.35 mm clearance per side,
                      marked with 1, 2, 3 dimples. 0.25 (2 dimples) is what the tiles use.
All 5 mm thick. Output: cad/out/
"""
from __future__ import annotations

import sys
from pathlib import Path

import manifold3d as m3

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
from a5_bed_cover import placed  # noqa: E402
from emcscan.cad import cyl, save  # noqa: E402

T = 5.0
FITS = [0.15, 0.25, 0.35]
PITCH = 35.0

out = HERE / "out"
knob = m3.CrossSection.square((25, 30)).translate((-25, -15)) + placed(0.0, 0, 0, 0)
save(m3.Manifold.extrude(knob, T).translate((25, 15, 0)), out / "a5_fit_knob.stl")

strip = m3.CrossSection.square((30, PITCH * len(FITS)))
for i, f in enumerate(FITS):
    strip = strip - placed(f, 0, PITCH * (i + 0.5), 0)
part = m3.Manifold.extrude(strip, T)
for i in range(len(FITS)):
    for k in range(i + 1):
        part = part - cyl(26.0, PITCH * i + 6 + k * 3.5, T - 0.6, 1, 2.0, 24)
save(part, out / "a5_fit_socket.stl")
