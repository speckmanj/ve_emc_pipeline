"""Run folder: everything one scan produces, in plain files you can open yourself.

runs/<date>-<time>_<job>/
    config.toml          exact config used (copy)
    plan.csv             every planned point: idx, grid indices, plate x/y/h, nozzle x/y/z
    meta.json            analyzer, LNA decision, start/end time, band frequencies file names
    freqs_b<k>.npy       frequency axis of band k (Hz)
    traces_b<k>.f32      raw sweeps of band k, float32, one row per measured point
    points.csv           one line per measured point (same order as traces): idx, time,
                         LNA, overload flag, peak level and peak frequency per band
    status.json          live progress, rewritten after every point (the viewer reads it)
    photo.png/.json      rectified board photo and its extent (if the job has one)
    report/              heat maps, spectra, report.html (from `emcscan report`)

Writing order per point: traces first, then the points.csv line. If the PC crashes,
the reader trusts points.csv and ignores any extra trace rows.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import time
from pathlib import Path

import numpy as np

from .plan import ScanPoint

PLAN_COLS = ["idx", "ix", "iy", "iz", "x", "y", "h", "nx", "ny", "nz"]


class RunWriter:
    def __init__(self, folder: Path, n_bands: int):
        self.dir = Path(folder)
        self.n_bands = n_bands

    @classmethod
    def create(cls, cfg: dict, plan: list[ScanPoint]) -> "RunWriter":
        from .config import resolve
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = "".join(c if c.isalnum() or c in "-_" else "_" for c in cfg["job"]["name"])
        root = resolve(cfg, cfg["output"]["runs_dir"]).resolve()
        folder = root / f"{stamp}_{name}"
        folder.mkdir(parents=True, exist_ok=False)
        shutil.copy(cfg["_path"], folder / "config.toml")
        with open(folder / "plan.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(PLAN_COLS)
            for p in plan:
                w.writerow([p.idx, p.ix, p.iy, p.iz, p.x, p.y, p.h,
                            round(p.nx, 3), round(p.ny, 3), round(p.nz, 3)])
        photo = cfg["job"].get("photo_rectified", "")
        if photo:
            src = resolve(cfg, photo)
            if src.exists() and src.with_suffix(".json").exists():
                shutil.copy(src, folder / "photo.png")
                shutil.copy(src.with_suffix(".json"), folder / "photo.json")
        n_bands = len(cfg["analyzer"]["bands"])
        hdr = ["idx", "t", "lna", "overload"]
        for k in range(n_bands):
            hdr += [f"b{k}_peak_dbm", f"b{k}_peak_hz"]
        with open(folder / "points.csv", "w", newline="") as fh:
            csv.writer(fh).writerow(hdr)
        return cls(folder, n_bands)

    @classmethod
    def open_existing(cls, folder: Path) -> "RunWriter":
        meta = json.loads((Path(folder) / "meta.json").read_text())
        rw = cls(Path(folder), len(meta["bands"]))
        # drop trace rows that have no points.csv line (crash between the two writes)
        n_done = len(read_points(folder))
        for k in range(rw.n_bands):
            f = rw.dir / f"traces_b{k}.f32"
            if f.exists():
                n = len(np.load(rw.dir / f"freqs_b{k}.npy"))
                keep = n_done * n * 4
                if f.stat().st_size > keep:
                    with open(f, "r+b") as fh:
                        fh.truncate(keep)
        return rw

    def write_meta(self, meta: dict):
        (self.dir / "meta.json").write_text(json.dumps(meta, indent=2))

    def save_freqs(self, k: int, freqs: np.ndarray):
        f = self.dir / f"freqs_b{k}.npy"
        if not f.exists():
            np.save(f, freqs)

    def append(self, p: ScanPoint, traces: list, lna: bool, overload: bool):
        row = [p.idx, f"{time.time():.2f}", int(lna), int(overload)]
        for k, (freqs, lv) in enumerate(traces):
            with open(self.dir / f"traces_b{k}.f32", "ab") as fh:
                fh.write(np.asarray(lv, dtype=np.float32).tobytes())
            i = int(np.argmax(lv))
            row += [f"{float(lv[i]):.2f}", f"{float(freqs[i]):.0f}"]
        with open(self.dir / "points.csv", "a", newline="") as fh:
            csv.writer(fh).writerow(row)

    def status(self, **kw):
        kw["updated"] = time.time()
        tmp = self.dir / "status.json.tmp"
        tmp.write_text(json.dumps(kw))
        for _ in range(20):                      # the viewer may hold the file for a moment
            try:
                os.replace(tmp, self.dir / "status.json")
                return
            except PermissionError:
                time.sleep(0.05)


# ---- reading -----------------------------------------------------------------

def crop_photo(img, extent, box):
    """Cut the rectified photo down to box = (x0, x1, y0, y1) in plate mm.
    Returns (image uint8 RGB, extent). Row 0 of the image is the BACK (max Y)."""
    ex0, ex1, ey0, ey1 = extent
    h, w = img.shape[:2]
    ppm_x, ppm_y = w / (ex1 - ex0), h / (ey1 - ey0)
    x0, x1 = max(box[0], ex0), min(box[1], ex1)
    y0, y1 = max(box[2], ey0), min(box[3], ey1)
    c0, c1 = int((x0 - ex0) * ppm_x), int((x1 - ex0) * ppm_x)
    r0, r1 = int((ey1 - y1) * ppm_y), int((ey1 - y0) * ppm_y)
    out = img[r0:r1, c0:c1, :3]
    if out.dtype != np.uint8:
        out = (np.clip(out, 0, 1) * 255).astype(np.uint8)
    return np.ascontiguousarray(out), [x0, x1, y0, y1]


def front_view(pl, box, top_z=15.0):
    """Camera as if standing in front of the printer, looking down at about 40 degrees."""
    x0, x1, y0, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    s = max(x1 - x0, y1 - y0)
    pl.camera_position = [(cx, cy - 1.25 * s, top_z + 1.1 * s), (cx, cy, top_z / 3), (0, 0, 1)]


def photo_texture_plane(img, extent, z=0.0):
    """A flat pyvista plane at height z carrying the photo. (pyvista flips rows itself.)"""
    import pyvista as pv
    x0, x1, y0, y1 = extent
    plane = pv.Plane(center=((x0 + x1) / 2, (y0 + y1) / 2, z), direction=(0, 0, 1),
                     i_size=x1 - x0, j_size=y1 - y0)
    plane.texture_map_to_plane(inplace=True)
    return plane, pv.numpy_to_texture(img)

def read_plan(folder) -> np.ndarray:
    """Plan as a structured numpy array with the PLAN_COLS fields."""
    return np.genfromtxt(Path(folder) / "plan.csv", delimiter=",", names=True)


def read_points(folder) -> list[dict]:
    f = Path(folder) / "points.csv"
    if not f.exists():
        return []
    with open(f, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return [r for r in rows if r.get("idx")]


def read_status(folder) -> dict:
    try:
        return json.loads((Path(folder) / "status.json").read_text())
    except (OSError, ValueError):
        return {}


class Run:
    """Everything in a run folder, loaded into numpy arrays for analysis."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self.meta = json.loads((self.dir / "meta.json").read_text())
        self.plan = read_plan(self.dir)
        self.points = read_points(self.dir)
        self.done_idx = np.array([int(r["idx"]) for r in self.points], dtype=int)
        self.bands = self.meta["bands"]
        self.freqs, self.traces = [], []
        n_done = len(self.done_idx)
        for k in range(len(self.bands)):
            fr = np.load(self.dir / f"freqs_b{k}.npy")
            raw = np.fromfile(self.dir / f"traces_b{k}.f32", dtype=np.float32)
            self.freqs.append(fr)
            self.traces.append(raw[: n_done * len(fr)].reshape(n_done, len(fr)))
        self.xs = np.unique(self.plan["x"])
        self.ys = np.unique(self.plan["y"])
        self.hs = np.unique(self.plan["h"])
        self.photo = None
        if (self.dir / "photo.png").exists():
            import matplotlib.image as mpimg
            self.photo = mpimg.imread(self.dir / "photo.png")
            self.photo_extent = json.loads((self.dir / "photo.json").read_text())["extent"]

    def grid(self, values: np.ndarray) -> np.ndarray:
        """Place one value per measured point into an array [height, row, col] (NaN = not measured)."""
        g = np.full((len(self.hs), len(self.ys), len(self.xs)), np.nan)
        pl = self.plan
        idx = self.done_idx
        g[pl["iz"][idx].astype(int), pl["iy"][idx].astype(int), pl["ix"][idx].astype(int)] = values
        return g

    def peak_grid(self, k: int) -> np.ndarray:
        return self.grid(self.traces[k].max(axis=1))

    def freq_grid(self, k: int, f_hz: float, half_width_bins: int = 1) -> np.ndarray:
        """Level at one frequency (max of the nearest bins) at every point."""
        i = int(np.argmin(np.abs(self.freqs[k] - f_hz)))
        lo, hi = max(i - half_width_bins, 0), i + half_width_bins + 1
        return self.grid(self.traces[k][:, lo:hi].max(axis=1))
