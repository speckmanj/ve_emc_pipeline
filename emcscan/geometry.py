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
   (angle_deg) and the plate top height (z_plate_top). The tip visits the back pair
   (markers 3 and 2) and marker 0 is worked out from the plate layout.

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
    """Z (printer frame, nozzle/tip reference) of the board top surface. Measured directly
    (calibration.z_board_top) when the board sits on pads above the plate."""
    zb = cfg["calibration"].get("z_board_top")
    if zb is not None:
        return float(zb)
    return plate_top_z(cfg) + cfg["board"]["thickness"]


def nozzle_below_tip(cfg) -> float:
    """How far a nozzle hangs below the probe tip (0 when the hotend is gone)."""
    if not cfg["printer"].get("has_nozzle", True):
        return 0.0
    return max(cfg["probe"]["offset_z"], 0.0)


def min_height(cfg) -> float:
    """Lowest legal probe height above the board.

    The nozzle and the probe both pass over the board, so BOTH must clear the
    tallest part plus the clearance margin. If the probe sits above the nozzle
    (offset_z > 0) the nozzle is the low point and sets the limit.
    """
    keep = cfg["board"]["tallest_part"] + cfg["scan"]["clearance"]
    return keep + nozzle_below_tip(cfg)


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


# Markers used to calibrate the plate. The back pair by default: with the probe mounted
# (no nozzle) the A5 cannot reach the front markers 0 and 1 (they sit in front of Y home).
CAL_PAIR_DEFAULT = (3, 2)       # back-left (4 dimples), then back-right (3 dimples)
PLATE_CAL_KEYS = ("origin_x", "origin_y", "angle_deg")


def cal_pair(cfg) -> tuple[int, int]:
    p = cfg.get("calibration", {}).get("cal_markers", CAL_PAIR_DEFAULT)
    return int(p[0]), int(p[1])


def plate_cal_from_pair(cfg, i: int, pos_i, j: int, pos_j) -> tuple[float, float, float, float]:
    """Nozzle X/Y with the tip over marker i, then over marker j -> where marker 0 is
    (origin_x, origin_y), the plate rotation (angle_deg) and the length error in mm
    (measured i-to-j distance minus the true one; over 1 mm = a marker or a jog is off).
    Marker 0 itself never has to be visited."""
    c = marker_centers_plate(cfg)
    (pix, piy), (pjx, pjy) = c[i], c[j]
    vx, vy = pos_j[0] - pos_i[0], pos_j[1] - pos_i[1]
    ux, uy = pjx - pix, pjy - piy
    a = math.atan2(vy, vx) - math.atan2(uy, ux)
    a = math.atan2(math.sin(a), math.cos(a))                    # wrap to -180..180
    ox = pos_i[0] - (math.cos(a) * pix - math.sin(a) * piy)
    oy = pos_i[1] - (math.sin(a) * pix + math.cos(a) * piy)
    err = math.hypot(vx, vy) - math.hypot(ux, uy)
    return round(ox, 2), round(oy, 2), round(math.degrees(a), 3), err
