"""Scan plan: the list of points the probe visits, and the safety checks.

The plan is a serpentine raster (left to right, then right to left on the next row)
at each height, so the printer never makes a long return move between rows.

Heights. Each layer has a nominal height h above the board top (scan.heights). With a
height setup (STEP model, taught marks, boxes or a master height; see heightmap.py) every
point gets its own tip height ht = max(h, lowest legal height there), so the probe stays
low over flat areas and rises only near tall parts. The move INTO each point climbs first
to vz (the highest legal height along the line from the previous point), then crosses,
then drops. Points with ht > h are "raised": their readings are weaker and the report
marks them. Without a setup the old rule applies: one flat height over everything, at
least tallest_part + clearance.
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
    ht: float = -1.0    # actual tip height above board top (>= h); -1 = same as h
    vz: float = -1e9    # nozzle Z to cross at when moving in from the previous point
    raised: bool = False

    def __post_init__(self):
        if self.ht < 0:
            self.ht = self.h


def grid_axes(cfg) -> tuple[np.ndarray, np.ndarray]:
    s = cfg["scan"]
    step = float(s["step"])
    xs = np.arange(s["x_min"], s["x_max"] + step * 0.01, step)
    ys = np.arange(s["y_min"], s["y_max"] + step * 0.01, step)
    return np.round(xs, 4), np.round(ys, 4)


AUTO = "auto"


class Plan(list):
    """List of ScanPoints plus the height map it was built with (None = flat rule)."""
    heights = None


def build_plan(cfg, heights=AUTO) -> list[ScanPoint]:
    """heights: a heightmap.Heights, None (flat rule), or AUTO (from the job's setup file)."""
    if heights is AUTO:
        from . import heightmap
        heights = heightmap.compute_for(cfg)
    xs, ys = grid_axes(cfg)
    plan = Plan()
    plan.heights = heights
    low = geometry.nozzle_below_tip(cfg)
    for iz, h in enumerate(cfg["scan"]["heights"]):
        for iy, y in enumerate(ys):
            cols = range(len(xs)) if iy % 2 == 0 else range(len(xs) - 1, -1, -1)
            for ix in cols:
                p = ScanPoint(len(plan), ix, iy, iz, float(xs[ix]), float(y), float(h))
                if heights is not None:
                    need = float(heights.req_at(p.x, p.y)) + low
                    if need > p.h + 1e-6:
                        p.ht, p.raised = round(need + 0.05, 2), True
                p.nx, p.ny, p.nz = geometry.plate_to_nozzle(cfg, p.x, p.y, p.ht)
                plan.append(p)
    for a, b in zip(plan, plan[1:]):
        b.vz = travel_z(cfg, heights, a, b)
    if plan:
        plan[0].vz = float(cfg["printer"]["safe_z"])
    return plan


def travel_z(cfg, heights, a: ScanPoint | None, b: ScanPoint) -> float:
    """Nozzle Z to cross at when moving from point a to point b."""
    if a is None:
        return float(cfg["printer"]["safe_z"])
    if heights is None:
        return max(a.nz, b.nz)
    v = heights.via(a.x, a.y, b.x, b.y) + geometry.nozzle_below_tip(cfg) + 0.05
    v = max(v, a.ht, b.ht)
    return max(geometry.plate_to_nozzle(cfg, b.x, b.y, v)[2], a.nz, b.nz)


def check_plan(cfg, plan: list[ScanPoint]) -> list[str]:
    """Return a list of problems. An empty list means the plan is safe to run."""
    errs = []
    pr = cfg["printer"]
    s = cfg["scan"]
    if s["x_max"] <= s["x_min"] or s["y_max"] <= s["y_min"]:
        errs.append("scan area is empty: x_max must exceed x_min and y_max must exceed y_min")
    if s["step"] <= 0:
        errs.append("scan.step must be positive")
    if not plan:
        errs.append("plan has no points")
        return errs
    heights = getattr(plan, "heights", None)
    if heights is None:
        hmin = geometry.min_height(cfg)
        for h in s["heights"]:
            if h < hmin - 1e-9:
                errs.append(
                    f"height {h} mm is too low: minimum is {hmin:.1f} mm "
                    f"(tallest_part {cfg['board']['tallest_part']} + clearance {s['clearance']}"
                    f" + nozzle below the tip {geometry.nozzle_below_tip(cfg)})")
    else:
        low = geometry.nozzle_below_tip(cfg)
        xs = np.array([p.x for p in plan]); ys = np.array([p.y for p in plan])
        need = heights.req_at(xs, ys) + low
        ht = np.array([p.ht for p in plan])
        bad = np.flatnonzero(ht < need - 1e-6)
        if len(bad):
            q = plan[int(bad[0])]
            errs.append(f"{len(bad)} point(s) below the legal height, first at "
                        f"({q.x:g}, {q.y:g}): {q.ht:.2f} < {need[bad[0]]:.2f} mm")
        for h in s["heights"]:
            if h < s["clearance"]:
                errs.append(f"height {h} mm is below the clearance {s['clearance']} mm")
        errs += [f"height map: {n}" for n in heights.notes]
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
    vz = np.array([p.vz for p in plan[1:]] or [nz.max()])
    if pr["safe_z"] < vz.max() - 1e-6:
        errs.append(f"printer.safe_z ({pr['safe_z']}) is below the highest travel Z ({vz.max():.1f}): "
                    "raise safe_z")
    if nz.min() < float(pr.get("z_min", -999)) - 1e-6:
        errs.append(f"nozzle Z would go down to {nz.min():.2f}, below the firmware limit z_min "
                    f"{pr['z_min']} (the printer would stop short and log the wrong height)")
    xmin, xmax, ymin, ymax = geometry.plate_extent(cfg)
    if s["x_min"] < xmin or s["x_max"] > xmax or s["y_min"] < ymin or s["y_max"] > ymax:
        errs.append("scan area extends beyond the plate")
    if str(pr.get("type", "sim")) != "sim":
        # Real hardware: refuse to run on template calibration. Before 2026-11-22 a job could
        # scan with the origin still at the template value and the board top a guess from the
        # nominal plate thickness: the probe would go to the wrong place at the wrong height.
        from .config import DEFAULTS
        cal, dc = cfg["calibration"], DEFAULTS["calibration"]
        if (float(cal.get("origin_x", dc["origin_x"])), float(cal.get("origin_y", dc["origin_y"]))) \
                == (float(dc["origin_x"]), float(dc["origin_y"])):
            errs.append(f"plate origin is still the template value ({dc['origin_x']:g}, "
                        f"{dc['origin_y']:g}): calibrate it (Setup > Machine) before scanning")
        if not pr.get("has_nozzle", True) and "z_board_top" not in cal:
            errs.append("board top Z is not calibrated (Setup > Machine > Set board top): probe "
                        "heights would be a guess from the nominal plate thickness")
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
        up = max(b.vz - a.nz, 0.0)
        down = max(b.vz - b.nz, 0.0) if b.vz > a.nz else abs(b.nz - a.nz)
        if up > 0:
            total += move_time(up, pr["feed_z"], pr["accel"]) + move_time(down, pr["feed_z"], pr["accel"])
        else:
            total += move_time(down, pr["feed_z"], pr["accel"])
        total += move_time(math.dist((a.nx, a.ny), (b.nx, b.ny)), pr["feed_xy"], pr["accel"])
    total += len(plan) * (pr["settle_s"] + n_bands * sweep_s_per_band)
    return total
