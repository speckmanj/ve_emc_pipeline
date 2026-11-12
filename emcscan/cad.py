"""Printable parts, generated from numbers (no CAD program needed).

    emcscan stl                         build every part into cad/out/
    emcscan stl --cad configs/cad.toml  same, with your measured sizes

Parts:
    spacer_plate.stl      lifts the board off the steel build plate; four pockets for the
                          paper markers; 1 to 4 dimples next to each pocket give its ID;
                          the arrow at the front edge points to the FRONT of the printer.
    probe_mount_x.stl     holds a TBPS01 probe vertically in front of the toolhead, probe
                          flat face parallel to the printer's X axis.
    probe_mount_y.stl     same, rotated 90 degrees (the other field component).
    probe_gauge.stl       steps of 2, 3, 4, 5 mm (2 to 5 dimples) to set how far the probe
                          tip sits above the nozzle tip (probe.offset_z).

The probe mount sticks to the toolhead with a removable adhesive strip (for example a
3M Command strip) on its flat back. Nothing on the printer is drilled or removed.
Print orientation is already correct: flat side down.

MEASURE FIRST: the probe shaft width and thickness (probe_w, probe_t) are first guesses.
"""
from __future__ import annotations

import math
import tomllib
from pathlib import Path

import manifold3d as m3
import numpy as np
import trimesh

from . import geometry

CAD_DEFAULTS = {
    "probe_w": 12.0,      # probe shaft width, mm (MEASURE with calipers)
    "probe_t": 1.6,       # probe shaft thickness, mm (MEASURE); round shaft: both = diameter
    "fit": 0.4,           # clearance added to the slot
    "back_w": 40.0,       # adhesive back plate, width
    "back_h": 40.0,       # adhesive back plate, height (also the clamp length along the probe)
    "back_t": 3.0,
    "standoff": 8.0,      # gap from toolhead face to the probe (keeps it off the toolhead cover)
    "wall": 3.0,
    "screw_pilot": 2.8,   # M3 screw self-taps into this hole (pinches the probe)
    "gauge_steps": [2.0, 3.0, 4.0, 5.0],
}


def box(x0, y0, z0, x1, y1, z1):
    return m3.Manifold.cube((x1 - x0, y1 - y0, z1 - z0)).translate((x0, y0, z0))


def cyl(x, y, z0, h, d, seg=48):
    return m3.Manifold.cylinder(h, d / 2, d / 2, seg).translate((x, y, z0))


def save(man: m3.Manifold, path: Path):
    mesh = man.to_mesh()
    tm = trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3],
                         faces=np.asarray(mesh.tri_verts), process=True)
    if not tm.is_watertight:
        raise RuntimeError(f"{path.name} is not watertight")
    tm.export(path)
    ext = tm.bounds[1] - tm.bounds[0]
    print(f"  {path.name:20s} {ext[0]:6.1f} x {ext[1]:6.1f} x {ext[2]:5.1f} mm")


def spacer_plate(cfg) -> m3.Manifold:
    p = cfg["plate"]
    w, d, h, dep = p["width"], p["depth"], p["height"], p["pocket_depth"]
    plate = box(0, 0, 0, w, d, h)
    ps = geometry.pocket_size(cfg)
    cut = []
    for i, (cx, cy) in geometry.marker_centers_body(cfg).items():
        cut.append(box(cx - ps / 2, cy - ps / 2, h - dep, cx + ps / 2, cy + ps / 2, h + 1))
        # ID dimples: i+1 dimples on a line from the pocket toward the plate center
        ux, uy = (w / 2 - cx), (d / 2 - cy)
        n = math.hypot(ux, uy); ux, uy = ux / n, uy / n
        for k in range(i + 1):
            r0 = ps / 2 * 1.41 + 3 + k * 3.5
            cut.append(cyl(cx + ux * r0, cy + uy * r0, h - 0.6, 1, 2.0, 24))
    # front arrow: a triangle engraved at the front edge, pointing to the front (-Y)
    tri = m3.CrossSection([[(w / 2, 2.0), (w / 2 + 6, 9.0), (w / 2 - 6, 9.0)]])  # counter-clockwise
    cut.append(m3.Manifold.extrude(tri, 1.0).translate((0, 0, h - 0.6)))
    for c in cut:
        plate = plate - c
    return plate


def probe_mount(c, orient: str) -> m3.Manifold:
    """Print orientation: adhesive back on the bed (z = 0). The probe slides along Y."""
    sw = c["probe_w"] + c["fit"]           # slot size along the probe's width
    st = c["probe_t"] + c["fit"]           # slot size along the probe's thickness
    W, H, T, so, wall = c["back_w"], c["back_h"], c["back_t"], c["standoff"], c["wall"]
    if orient == "x":                      # probe flat face parallel to the back plate
        sx, sz = sw, st
    else:                                  # probe flat face perpendicular to the back plate
        sx, sz = st, sw
    cw = sx + 2 * wall
    z_slot0 = T + so
    z_top = z_slot0 + sz + wall
    body = box(0, 0, 0, W, H, T) + box(W / 2 - cw / 2, 0, T, W / 2 + cw / 2, H, z_top)
    body = body - box(W / 2 - sx / 2, -1, z_slot0, W / 2 + sx / 2, H + 1, z_slot0 + sz)
    for yy in (H * 0.3, H * 0.7):          # two pinch screws
        if orient == "x":                  # screw from the top, presses the flat face
            body = body - cyl(W / 2, yy, z_slot0 + sz - 0.1, wall + 1, c["screw_pilot"])
        else:                              # screw from the side, presses the flat face
            hole = m3.Manifold.cylinder(wall + 1, c["screw_pilot"] / 2, c["screw_pilot"] / 2, 48)
            hole = hole.rotate((0, 90, 0)).translate((W / 2 + sx / 2 - 0.1, yy, z_slot0 + sz / 2))
            body = body - hole
    return body


def probe_gauge(c) -> m3.Manifold:
    steps = c["gauge_steps"]
    g = None
    for i, s in enumerate(steps):
        b = box(i * 12, 0, 0, i * 12 + 12, 20, s)
        g = b if g is None else g + b
    for i, s in enumerate(steps):          # s dimples in a row along the BACK edge of a step of s mm;
        n = int(round(s))                  # the front 14 mm stays flat for the probe tip
        for k in range(n):
            g = g - cyl(i * 12 + 6 + (k - (n - 1) / 2) * 2.1, 17.5, s - 0.4, 1, 1.2, 16)
    return g


def build_all(cfg, out_dir: Path, cad_toml: str | None = None) -> None:
    c = dict(CAD_DEFAULTS)
    if cad_toml:
        with open(cad_toml, "rb") as fh:
            c.update(tomllib.load(fh).get("cad", {}))
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing STL files to {out_dir}")
    save(spacer_plate(cfg), out_dir / "spacer_plate.stl")
    save(probe_mount(c, "x"), out_dir / "probe_mount_x.stl")
    save(probe_mount(c, "y"), out_dir / "probe_mount_y.stl")
    save(probe_gauge(c), out_dir / "probe_gauge.stl")
