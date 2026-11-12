"""Scan plan: the list of points the probe visits, and the safety checks.

The plan is a serpentine raster (left to right, then right to left on the next row)
at each height, so the printer never makes a long return move between rows.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import geometry


@dataclass
class ScanPoint:
    idx: int            # position in the plan, 0..N-1
    ix: int             # grid column
    iy: int             # grid row
    iz: int             # height index
    x: float            # plate frame, mm
    y: float
    h: float            # probe tip height above board top, mm
    nx: float = 0.0     # printer nozzle coordinates, mm
    ny: float = 0.0
    nz: float = 0.0


def grid_axes(cfg) -> tuple[np.ndarray, np.ndarray]:
    s = cfg["scan"]
    step = float(s["step"])
    xs = np.arange(s["x_min"], s["x_max"] + step * 0.01, step)
    ys = np.arange(s["y_min"], s["y_max"] + step * 0.01, step)
    return np.round(xs, 4), np.round(ys, 4)


def build_plan(cfg) -> list[ScanPoint]:
    xs, ys = grid_axes(cfg)
    plan: list[ScanPoint] = []
    for iz, h in enumerate(cfg["scan"]["heights"]):
        for iy, y in enumerate(ys):
            cols = range(len(xs)) if iy % 2 == 0 else range(len(xs) - 1, -1, -1)
            for ix in cols:
                p = ScanPoint(len(plan), ix, iy, iz, float(xs[ix]), float(y), float(h))
                p.nx, p.ny, p.nz = geometry.plate_to_nozzle(cfg, p.x, p.y, p.h)
                plan.append(p)
    return plan


def check_plan(cfg, plan: list[ScanPoint]) -> list[str]:
    """Return a list of problems. An empty list means the plan is safe to run."""
    errs = []
    pr = cfg["printer"]
    s = cfg["scan"]
    if s["x_max"] <= s["x_min"] or s["y_max"] <= s["y_min"]:
        errs.append("scan area is empty: x_max must exceed x_min and y_max must exceed y_min")
    if s["step"] <= 0:
        errs.append("scan.step must be positive")
    hmin = geometry.min_height(cfg)
    for h in s["heights"]:
        if h < hmin - 1e-9:
            errs.append(
                f"height {h} mm is too low: minimum is {hmin:.1f} mm "
                f"(tallest_part {cfg['board']['tallest_part']} + clearance {s['clearance']}"
                f" + probe above nozzle {max(cfg['probe']['offset_z'], 0)})")
    if not plan:
        errs.append("plan has no points")
        return errs
    nx = np.array([p.nx for p in plan]); ny = np.array([p.ny for p in plan])
    nz = np.array([p.nz for p in plan])
    if nx.min() < 0 or nx.max() > pr["bed_x"]:
        errs.append(f"nozzle X would run {nx.min():.1f} to {nx.max():.1f} mm, outside 0..{pr['bed_x']}")
    if ny.min() < 0 or ny.max() > pr["bed_y"]:
        errs.append(f"nozzle Y would run {ny.min():.1f} to {ny.max():.1f} mm, outside 0..{pr['bed_y']}")
    if nz.max() > pr["z_max"]:
        errs.append(f"nozzle Z would reach {nz.max():.1f} mm, above z_max {pr['z_max']}")
    if pr["safe_z"] < nz.max():
        errs.append(f"printer.safe_z ({pr['safe_z']}) is below the highest scan Z ({nz.max():.1f})")
    xmin, xmax, ymin, ymax = geometry.plate_extent(cfg)
    if s["x_min"] < xmin or s["x_max"] > xmax or s["y_min"] < ymin or s["y_max"] > ymax:
        errs.append("scan area extends beyond the plate")
    return errs


def move_time(dist_mm: float, feed_mm_min: float, accel: float) -> float:
    """Time for one straight move with a trapezoid speed profile."""
    if dist_mm <= 0:
        return 0.0
    v = feed_mm_min / 60.0
    d_acc = v * v / accel                    # distance to speed up AND slow down
    if dist_mm >= d_acc:
        return dist_mm / v + v / accel
    return 2.0 * math.sqrt(dist_mm / accel)


def estimate_seconds(cfg, plan: list[ScanPoint], sweep_s_per_band: float = 0.5) -> float:
    pr = cfg["printer"]
    n_bands = len(cfg["analyzer"]["bands"])
    total = 0.0
    for a, b in zip(plan, plan[1:]):
        d = math.dist((a.nx, a.ny, a.nz), (b.nx, b.ny, b.nz))
        total += move_time(d, pr["feed_xy"], pr["accel"])
    total += len(plan) * (pr["settle_s"] + n_bands * sweep_s_per_band)
    return total
