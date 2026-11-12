"""Coordinate frames.

There are three frames. Keeping them straight is most of the job:

1. PLATE frame (what you think in): millimeters on the printed spacer plate.
   Origin = center of marker 0 (front-left). +X to the right, +Y toward the back.
   Height h = probe tip height ABOVE THE BOARD TOP SURFACE.
   Scan areas, heat maps and reports all use this frame.

2. BODY frame (only for CAD and photos): same axes, but the origin is the plate's
   front-left CORNER. body = plate + marker0_center_body.

3. PRINTER frame (what the printer is told): nozzle X, Y, Z in printer coordinates.
   `emcscan calibrate` measures where marker 0 is (origin_x/y), the plate rotation
   (angle_deg) and the plate top height (z_plate_top).

    nozzle_xy = origin + R(angle) * plate_xy
    nozzle_z  = board_top_z + h - probe.offset_z
"""
from __future__ import annotations

import math


# ---- plate and marker layout -------------------------------------------------

def label_size(cfg) -> float:
    p = cfg["plate"]
    return p["marker_size"] + 2 * p["marker_border"]


def pocket_size(cfg) -> float:
    return label_size(cfg) + 0.6          # 0.3 mm clearance per side for the paper


def marker_centers_body(cfg) -> dict[int, tuple[float, float]]:
    """Marker centers measured from the plate's front-left corner.
    IDs: 0 front-left, 1 front-right, 2 back-right, 3 back-left."""
    p = cfg["plate"]
    c = p["marker_inset"] + pocket_size(cfg) / 2
    w, d = p["width"], p["depth"]
    return {0: (c, c), 1: (w - c, c), 2: (w - c, d - c), 3: (c, d - c)}


def marker_centers_plate(cfg) -> dict[int, tuple[float, float]]:
    body = marker_centers_body(cfg)
    ox, oy = body[0]
    return {k: (x - ox, y - oy) for k, (x, y) in body.items()}


def plate_extent(cfg) -> tuple[float, float, float, float]:
    """(x_min, x_max, y_min, y_max) of the whole plate in the plate frame."""
    ox, oy = marker_centers_body(cfg)[0]
    p = cfg["plate"]
    return (-ox, p["width"] - ox, -oy, p["depth"] - oy)


# ---- heights -------------------------------------------------------------------

def plate_top_z(cfg) -> float:
    """Nozzle Z at which the nozzle touches the plate top (calibrated, else nominal)."""
    return float(cfg["calibration"].get("z_plate_top", cfg["plate"]["height"]))


def board_top_z(cfg) -> float:
    return plate_top_z(cfg) + cfg["board"]["thickness"]


def min_height(cfg) -> float:
    """Lowest legal probe height above the board.

    The nozzle and the probe both pass over the board, so BOTH must clear the
    tallest part plus the clearance margin. If the probe sits above the nozzle
    (offset_z > 0) the nozzle is the low point and sets the limit.
    """
    keep = cfg["board"]["tallest_part"] + cfg["scan"]["clearance"]
    return keep + max(cfg["probe"]["offset_z"], 0.0)


# ---- plate -> printer ------------------------------------------------------------

def plate_to_nozzle(cfg, x: float, y: float, h: float) -> tuple[float, float, float]:
    cal = cfg["calibration"]
    a = math.radians(cal["angle_deg"])
    nx = cal["origin_x"] + math.cos(a) * x - math.sin(a) * y
    ny = cal["origin_y"] + math.sin(a) * x + math.cos(a) * y
    nz = board_top_z(cfg) + h - cfg["probe"]["offset_z"]
    return nx, ny, nz


def nozzle_to_plate_xy(cfg, nx: float, ny: float) -> tuple[float, float]:
    cal = cfg["calibration"]
    a = math.radians(cal["angle_deg"])
    dx, dy = nx - cal["origin_x"], ny - cal["origin_y"]
    return (math.cos(a) * dx + math.sin(a) * dy, -math.sin(a) * dx + math.cos(a) * dy)
