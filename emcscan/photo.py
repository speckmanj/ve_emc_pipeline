"""Board photo -> flat, to-scale image in plate coordinates.

You take a phone photo of the plate from roughly above (any angle that shows all four
paper markers). OpenCV finds markers 0..3 (ArUco 4x4 dictionary), and because we know
exactly where their centers are on the plate, a perspective transform (homography)
maps the photo onto the plate frame at `px_per_mm` pixels per millimeter.

The result is photo.png plus photo.json with its extent [x_min, x_max, y_min, y_max]
in plate millimeters. Heat maps are drawn on top of it with the same extent, so a
hot spot in the map sits on the part that caused it.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from . import geometry

DICT = cv2.aruco.DICT_4X4_50


def detect_markers(img_bgr) -> dict[int, np.ndarray]:
    """Return {marker_id: center_pixel_xy} for every marker found."""
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY) if img_bgr.ndim == 3 else img_bgr
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(DICT),
                                  cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(gray)
    found = {}
    if ids is not None:
        for c, i in zip(corners, ids.flatten()):
            found[int(i)] = c.reshape(4, 2).mean(axis=0)
    return found


def rectify(cfg, img_bgr, px_per_mm: float = 8.0):
    """Warp the photo into the plate frame. Returns (image_rgb, extent, used_ids)."""
    found = detect_markers(img_bgr)
    need = [0, 1, 2, 3]
    missing = [i for i in need if i not in found]
    if missing:
        raise RuntimeError(f"markers not found in the photo: {missing} (found {sorted(found)}). "
                           "Check lighting, glare and that all four are in view.")
    x0, x1, y0, y1 = geometry.plate_extent(cfg)
    centers = geometry.marker_centers_plate(cfg)
    src = np.array([found[i] for i in need], dtype=np.float32)
    # plate mm -> output pixel: column = (x - x0) * ppm, row = (y1 - y) * ppm (rows go down)
    dst = np.array([[(centers[i][0] - x0) * px_per_mm, (y1 - centers[i][1]) * px_per_mm]
                    for i in need], dtype=np.float32)
    H, _ = cv2.findHomography(src, dst, 0)
    w, h = int(round((x1 - x0) * px_per_mm)), int(round((y1 - y0) * px_per_mm))
    out = cv2.warpPerspective(img_bgr, H, (w, h))
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB), [x0, x1, y0, y1], need


def save_rectified(img_rgb, extent, out_png: Path):
    out_png = Path(out_png)
    cv2.imwrite(str(out_png), cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR))
    out_png.with_suffix(".json").write_text(json.dumps({"extent": extent}))


def pick_area(img_rgb, extent, margin: float = 2.0):
    """Show the rectified photo; you click two opposite corners of the board."""
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 9))
    ax.imshow(img_rgb, extent=extent)
    ax.set_xlabel("X plate (mm)"); ax.set_ylabel("Y plate (mm)")
    ax.set_title("Click two opposite corners of the area to scan, then close the window")
    pts = plt.ginput(2, timeout=0)
    plt.close(fig)
    if len(pts) != 2:
        return None
    (ax_, ay), (bx, by) = pts
    r = lambda v: round(v * 2) / 2
    return (r(min(ax_, bx) - margin), r(max(ax_, bx) + margin),
            r(min(ay, by) - margin), r(max(ay, by) + margin))
