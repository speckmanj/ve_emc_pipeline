"""3D preview of a job before scanning:  emcscan preview <job.toml>

Shows the board (STEP model placed on the plate, or the board photo), taught marks and
boxes, and the planned probe path colored by tip height. Raised points (lifted over a
tall part) are larger. Opens its own window, so the app stays responsive.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


def show(cfg_path: str | Path, screenshot: str | None = None):
    import pyvista as pv

    from . import config, heightmap
    from .config import resolve
    from .plan import build_plan

    cfg = config.load(cfg_path)
    setup = heightmap.load_setup(cfg) or heightmap.empty_setup()
    plan = build_plan(cfg)
    pl = pv.Plotter(title=f"emcscan preview: {cfg['job']['name']}", off_screen=bool(screenshot),
                    window_size=(1200, 850))
    st = setup.get("step")
    if st and st.get("file"):
        board = heightmap.load_step(resolve(cfg, st["file"]), bool(st.get("flip")),
                                    cache_dir=Path(cfg["_dir"]))
        th, t, _ = heightmap.registration(st.get("reg") or {})
        V = heightmap.board_to_plate(board.V, th, t)
        faces = np.hstack([np.full((len(board.F), 1), 3), board.F]).ravel()
        pl.add_mesh(pv.PolyData(V, faces), color="#4c7a52", opacity=0.9, smooth_shading=False)
    else:
        photo = cfg["job"].get("photo_rectified")
        if photo and resolve(cfg, photo).exists():
            import json

            import matplotlib.image as mpimg

            from .runstore import crop_photo, photo_texture_plane
            img = mpimg.imread(resolve(cfg, photo))
            ext = json.loads(resolve(cfg, photo).with_suffix(".json").read_text())["extent"]
            s = cfg["scan"]
            img, ext = crop_photo(img, ext, (s["x_min"] - 15, s["x_max"] + 15,
                                             s["y_min"] - 15, s["y_max"] + 15))
            plane, tex = photo_texture_plane(img, ext)
            pl.add_mesh(plane, texture=tex)
    for m in setup.get("marks") or []:
        pl.add_mesh(pv.Cylinder(center=(m["x"], m["y"], m["h"] / 2), direction=(0, 0, 1),
                                radius=m.get("r", 3.0), height=m["h"]), color="orange", opacity=0.6)
    for b in setup.get("boxes") or []:
        pl.add_mesh(pv.Box(bounds=(min(b["x0"], b["x1"]), max(b["x0"], b["x1"]),
                                   min(b["y0"], b["y1"]), max(b["y0"], b["y1"]), 0, b["h"])),
                    color="orange", opacity=0.5)
    pts = np.array([[p.x, p.y, p.ht] for p in plan])
    if len(pts) > 1:
        line = pv.lines_from_points(pts)
        line["tip height (mm)"] = pts[:, 2]
        pl.add_mesh(line, scalars="tip height (mm)", cmap="plasma", line_width=2)
        raised = np.array([p.raised for p in plan])
        if raised.any():
            pl.add_mesh(pv.PolyData(pts[raised]), color="red", point_size=7,
                        render_points_as_spheres=True, label="raised")
        pl.add_mesh(pv.PolyData(pts[:1]), color="lime", point_size=14, render_points_as_spheres=True)
    pl.show_grid(xtitle="X plate (mm)", ytitle="Y plate (mm)", ztitle="mm above board")
    s = cfg["scan"]
    from .runstore import front_view
    front_view(pl, (s["x_min"], s["x_max"], s["y_min"], s["y_max"]), float(pts[:, 2].max()) if len(pts) else 10)
    pl.show(screenshot=screenshot, auto_close=bool(screenshot))
