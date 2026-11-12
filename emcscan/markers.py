"""Printable marker sheet (paper labels that go into the plate's four pockets).

Prints at exact size on US Letter. PRINT AT 100% / "Actual size" - not "Fit to page".
Check the 50 mm bar with a ruler before cutting.

Each label = black ArUco square (marker_size) + white quiet zone (marker_border).
Cut on the thin gray line. The small number under each label is its ID: 0 goes in the
front-left pocket (one dimple), 1 front-right (two), 2 back-right (three), 3 back-left
(four). Rotation of a label does not matter; only the ID-to-pocket match does.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                     # noqa: E402
import numpy as np                                  # noqa: E402

from . import geometry                              # noqa: E402
from .photo import DICT                             # noqa: E402

PAGE_W, PAGE_H = 215.9, 279.4                       # US Letter, mm


def make_sheet(cfg, out_dir: Path, copies: int = 2) -> list[Path]:
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    s = cfg["plate"]["marker_size"]
    lab = geometry.label_size(cfg)
    d = cv2.aruco.getPredefinedDictionary(DICT)
    fig = plt.figure(figsize=(PAGE_W / 25.4, PAGE_H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, PAGE_W); ax.set_ylim(0, PAGE_H); ax.axis("off")
    gap = 12.0
    for c in range(copies):
        for i in range(4):
            x0 = 20 + i * (lab + gap)
            y0 = PAGE_H - 30 - lab - c * (lab + 2 * gap)
            ax.add_patch(plt.Rectangle((x0, y0), lab, lab, fill=False, ec="#999999", lw=0.3))
            img = cv2.aruco.generateImageMarker(d, i, 600)
            b = cfg["plate"]["marker_border"]
            ax.imshow(img, cmap="gray", vmin=0, vmax=255, interpolation="nearest",
                      extent=(x0 + b, x0 + b + s, y0 + b, y0 + b + s))
            ax.text(x0 + lab / 2, y0 - 4, f"ID {i}", ha="center", va="top", fontsize=7)
    yb = 40
    ax.plot([20, 70], [yb, yb], "k-", lw=1.5)
    for xx in (20, 70):
        ax.plot([xx, xx], [yb - 2, yb + 2], "k-", lw=1.5)
    ax.text(45, yb + 3, "50 mm: measure this before cutting (print at 100%)", ha="center", fontsize=8)
    notes = ["Valence EMC scanner plate markers  -  ArUco 4x4, IDs 0-3. Cut on the gray line.",
             "Glue-stick into the pockets: ID 0 front-left (1 dimple), ID 1 front-right (2),",
             "ID 2 back-right (3), ID 3 back-left (4). Label rotation does not matter."]
    ax.text(20, 25, "\n".join(notes), fontsize=7, va="top")
    outs = [out_dir / "markers_letter.pdf", out_dir / "markers_letter.png"]
    fig.savefig(outs[0])
    fig.savefig(outs[1], dpi=300)
    plt.close(fig)
    return outs


def render_plate_image(cfg, px_per_mm: float = 6.0) -> np.ndarray:
    """Top-view image of the plate with markers in place (used to test photo.py)."""
    w, d = cfg["plate"]["width"], cfg["plate"]["depth"]
    img = np.full((int(d * px_per_mm), int(w * px_per_mm)), 170, np.uint8)
    dct = cv2.aruco.getPredefinedDictionary(DICT)
    s, b = cfg["plate"]["marker_size"], cfg["plate"]["marker_border"]
    for i, (cx, cy) in geometry.marker_centers_body(cfg).items():
        lab = int((s + 2 * b) * px_per_mm)
        m = cv2.aruco.generateImageMarker(dct, i, int(s * px_per_mm))
        tile = np.full((lab, lab), 255, np.uint8)
        o = int(b * px_per_mm)
        tile[o:o + m.shape[0], o:o + m.shape[1]] = m
        c, r = int(cx * px_per_mm - lab / 2), int((d - cy) * px_per_mm - lab / 2)
        img[r:r + lab, c:c + lab] = tile
    return img
