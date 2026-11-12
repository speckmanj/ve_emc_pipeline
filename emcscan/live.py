"""Live 3D viewer (pyvista). Watches a run folder and redraws about once a second.

    emcscan live runs/<run>            follow a scan in progress (or view a finished one)
    emcscan live runs/<run> --band 1   color by band 1 instead of the strongest band

What you see (plate frame, mm; Z = height above the board top):
    - the board photo at Z = 0 (if the job has one), plate outline otherwise
    - the planned path as a thin gray line, one layer per height
    - measured points as colored squares (peak level in dBm)
    - a red ball at the probe's latest position
    - a status line: progress, ETA, latest peak, LNA, state

The viewer only READS files. Closing it never affects the scan.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pyvista as pv

from .runstore import read_plan, read_points, read_status


def _fmt_s(s):
    s = int(max(s or 0, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class LiveView:
    def __init__(self, run_dir: str | Path, band: int | None = None, off_screen: bool = False):
        self.dir = Path(run_dir)
        self.band = band
        self.plan = read_plan(self.dir)
        self.n_seen = -1
        self.pl = pv.Plotter(title=f"EMC scan - {self.dir.name}", window_size=(1280, 860),
                             off_screen=off_screen)
        self._static_scene()

    def _static_scene(self):
        pl, plan = self.pl, self.plan
        step = float(np.min(np.diff(np.unique(plan["x"])))) if len(np.unique(plan["x"])) > 1 else 2.0
        self.step = step
        photo = self.dir / "photo.png"
        box = (plan["x"].min() - 10, plan["x"].max() + 10, plan["y"].min() - 10, plan["y"].max() + 10)
        if photo.exists():
            import matplotlib.image as mpimg
            from .runstore import crop_photo, photo_texture_plane
            ext = json.loads((self.dir / "photo.json").read_text())["extent"]
            img, ext = crop_photo(mpimg.imread(photo), ext, box)
            plane, tex = photo_texture_plane(img, ext)
            pl.add_mesh(plane, texture=tex)
        else:
            x0, x1, y0, y1 = box
            pl.add_mesh(pv.Plane(center=((x0 + x1) / 2, (y0 + y1) / 2, 0),
                                 i_size=x1 - x0, j_size=y1 - y0), color="#1b5e20", opacity=0.6)
        for h in np.unique(plan["h"]):
            sel = plan[plan["h"] == h]
            pts = np.column_stack([sel["x"], sel["y"], sel["h"]])
            pl.add_mesh(pv.lines_from_points(pts), color="#9e9e9e", line_width=1, opacity=0.5)
        pl.add_axes()
        pl.show_grid(xtitle="X plate (mm)", ytitle="Y plate (mm)", ztitle="height (mm)", n_zlabels=3, fmt="%.0f")
        from .runstore import front_view
        front_view(pl, box, float(plan["h"].max()))

    def refresh(self, *_):
        pts = read_points(self.dir)
        st = read_status(self.dir)
        if len(pts) != self.n_seen and pts:
            self.n_seen = len(pts)
            idx = np.array([int(r["idx"]) for r in pts])
            nb = sum(1 for k in pts[0] if k.endswith("_peak_dbm"))
            levels = np.array([[float(r[f"b{k}_peak_dbm"]) for k in range(nb)] for r in pts])
            if self.band is None:
                val, label = levels.max(axis=1), "peak dBm (all bands)"
            else:
                val, label = levels[:, self.band], f"peak dBm (band {self.band})"
            xyz = np.column_stack([self.plan["x"][idx], self.plan["y"][idx], self.plan["h"][idx]])
            cloud = pv.PolyData(xyz)
            cloud["dBm"] = val
            self.pl.add_mesh(cloud, name="data", scalars="dBm", cmap="inferno",
                             point_size=max(6, int(self.step * 4)), render_points_as_spheres=False,
                             clim=[float(val.min()), max(float(val.max()), float(val.min()) + 1)],
                             scalar_bar_args={"title": label})
            self.pl.add_mesh(pv.Sphere(radius=max(self.step * 0.6, 0.8), center=xyz[-1]),
                             name="probe", color="red")
        total = st.get("total", len(self.plan))
        done = st.get("done", len(pts))
        line = (f"{st.get('state', 'waiting')}  {done}/{total} ({100 * done / max(total, 1):.1f}%)"
                f"  ETA {_fmt_s(st.get('eta_s'))}")
        if "peak_dbm" in st:
            line += (f"   last: {st['peak_dbm']:.1f} dBm @ {st['peak_hz'] / 1e6:.2f} MHz"
                     f"   LNA {'on' if st.get('lna') else 'off'}")
        self.pl.add_text(line, name="status", position="upper_left", font_size=10)

    def show(self, screenshot: str | None = None):
        self.refresh()
        if screenshot:                          # used by tests and the report
            self.pl.show(screenshot=screenshot, auto_close=True)
            return
        # Redraw every second until the window is closed.
        try:
            self.pl.add_timer_event(max_steps=10 ** 7, duration=1000, callback=self.refresh)
            self.pl.show()
        except AttributeError:                  # very old pyvista: manual loop
            self.pl.show(interactive_update=True, auto_close=False)
            while not getattr(self.pl, "_closed", False):
                self.refresh()
                self.pl.update()
                time.sleep(1.0)


def main(run_dir, band=None, screenshot=None):
    LiveView(run_dir, band, off_screen=bool(screenshot)).show(screenshot)
