"""Height map: how high the probe tip must be, everywhere over the board.

Sources (any combination; the highest one wins at every spot):
    STEP   the customer's 3D model of the assembled board (exact parts, no typing)
    marks  points taught by jogging the probe tip to just above the top of a tall part
    boxes  rectangles with a height, drawn on the board photo
    flat   one master height for the whole area (low-profile boards: tallest part)

All of it lives in the job's setup file (JSON, next to the job TOML, edited by the app):

    {"flat_mm": 0.0,
     "step":  {"file": "Main Board.step", "flip": false,
               "reg": {"b": [[x, y], [x, y]], "p": [[x, y], [x, y]], "rot_deg": 0}},
     "marks": [{"x": 31.0, "y": 22.5, "h": 11.2, "r": 3.0, "note": "C12"}],
     "boxes": [{"x0": 10, "x1": 22, "y0": 5, "y1": 14, "h": 9.0, "note": "J1"}],
     "margin_xy": 1.5}

Frames. The STEP has its own BOARD frame (origin: front-left corner of the PCB outline,
z = 0 on the PCB top face). Registration places it on the PLATE frame: two board points
(b) and the same two points on the plate (p), clicked on the photo or found by jogging the
probe tip onto them. One point plus a rotation (0/90/180/270) also works.

Probe shape. The probe hangs straight down, so seen from above an H-field ring covers a
LINE (ring width x blade thickness), not a disc, and its bottom is round: at u mm from
the center the ring bottom is R - sqrt(R^2 - u^2) above the tip. The required tip height
at a point is therefore
    max over the probe footprint of ( height under that spot - probe bottom there ) + clearance
(a grey dilation of the height map with the probe profile), done separately for each
blade orientation. The probe mount and head are treated as a disc of radius head_r whose
underside is head_h above the tip, so a very tall part cannot hit the mount either.

Everything is conservative: triangles are splatted to every cell their bounding box
touches, maps are sampled at the highest neighbouring cell, and travel moves use the
highest point along the line between two scan points.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RES = 0.5                                   # height map cell, mm

# TekBox TBPS01 tips: width = widest part seen from the side, height = how far up from the
# tip end that width reaches; ring = round bottom (H-field loops).
PROBE_TIPS = {
    "H20": {"tip_w": 23.8, "tip_h": 24.0, "ring": True},    # calipers, Jacob, 2026-11-17
    "H10": {"tip_w": 14.0, "tip_h": 14.0, "ring": True},    # ESTIMATE: measure
    "H5": {"tip_w": 9.25, "tip_h": 8.0, "ring": False},     # blade width governs
    "E5": {"tip_w": 9.25, "tip_h": 8.0, "ring": False},
}
BLADE_T = 2.0                               # blade and ring thickness, calipers


# ---- setup file ------------------------------------------------------------------------

def setup_path(cfg) -> Path:
    from .config import resolve
    name = cfg["job"].get("setup") or (Path(cfg["_path"]).stem + "_setup.json")
    return resolve(cfg, name)


def empty_setup() -> dict:
    return {"flat_mm": 0.0, "step": None, "marks": [], "boxes": [], "margin_xy": 1.5}


def load_setup(cfg) -> dict | None:
    p = setup_path(cfg)
    if not p.exists():
        return None
    s = empty_setup()
    s.update(json.loads(p.read_text(encoding="utf-8")))
    return s


def save_setup(cfg, setup: dict) -> Path:
    p = setup_path(cfg)
    p.write_text(json.dumps(setup, indent=2), encoding="utf-8")
    return p


def uses_heights(setup: dict | None) -> bool:
    if not setup:
        return False
    return bool(setup.get("step") or setup.get("marks") or setup.get("boxes")
                or float(setup.get("flat_mm") or 0) > 0)


# ---- STEP --------------------------------------------------------------------------------

@dataclass
class StepBoard:
    V: np.ndarray            # vertices, board frame, mm (z = 0 on the PCB top)
    F: np.ndarray            # triangles
    thickness: float         # PCB thickness found in the model
    size: tuple              # PCB outline bounding box (w, d), mm
    tallest: float           # highest point above the PCB top, mm
    n_parts: int
    source: str
    flip: bool

    def corners(self) -> dict[str, tuple[float, float]]:
        w, d = self.size
        return {"front-left": (0.0, 0.0), "front-right": (w, 0.0),
                "back-right": (w, d), "back-left": (0.0, d)}


def _tri_normals(T):
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    a = np.linalg.norm(n, axis=1)
    return n, a


def load_step(path: str | Path, flip: bool = False, cache_dir: Path | None = None) -> StepBoard:
    """Read a STEP file (via trimesh + cascadio/OpenCascade), find the PCB, return it in the
    board frame. flip=True turns the board over (to scan the bottom side)."""
    path = Path(path)
    cache = None
    if cache_dir is not None:
        cache = Path(cache_dir) / f".{path.stem}_{'bot' if flip else 'top'}.npz"
        if cache.exists() and cache.stat().st_mtime > path.stat().st_mtime:
            z = np.load(cache, allow_pickle=False)
            m = json.loads(str(z["meta"]))
            return StepBoard(z["V"], z["F"], m["thickness"], tuple(m["size"]), m["tallest"],
                             m["n_parts"], str(path), flip)
    import trimesh
    scene = trimesh.load(str(path), force="scene")
    n_parts = len(scene.geometry)
    mesh = scene.to_mesh() if hasattr(scene, "to_mesh") else scene.dump(concatenate=True)
    V = np.asarray(mesh.vertices, dtype=np.float64)
    F = np.asarray(mesh.faces, dtype=np.int64)
    if np.ptp(V, axis=0).max() < 2.0:          # cascadio writes meters
        V = V * 1000.0
    if flip:                                    # turn over about the X axis
        V = V * np.array([1.0, -1.0, -1.0])
    T = V[F]
    n, a = _tri_normals(T)
    ok = a > 1e-12
    nz = np.zeros(len(F)); nz[ok] = n[ok, 2] / a[ok]
    zc = T[:, :, 2].mean(axis=1)
    key = np.round(zc / 0.01).astype(np.int64)

    def biggest(mask, lo=-np.inf, hi=np.inf):
        m = mask & (zc > lo) & (zc < hi)
        if not m.any():
            return None
        ks, inv = np.unique(key[m], return_inverse=True)
        area = np.bincount(inv, weights=a[m] / 2)
        return ks[int(np.argmax(area))] * 0.01

    z_top = biggest(nz > 0.985)
    if z_top is None:
        raise ValueError("no flat upward face found: is this a board model?")
    z_bot = biggest(nz < -0.985, z_top - 6.0, z_top - 0.2)
    thickness = float(z_top - z_bot) if z_bot is not None else float("nan")
    pcb = (nz > 0.985) & (np.abs(zc - z_top) < 0.02)
    pv = T[pcb].reshape(-1, 3)
    x0, y0 = pv[:, 0].min(), pv[:, 1].min()
    x1, y1 = pv[:, 0].max(), pv[:, 1].max()
    V = V - np.array([x0, y0, z_top])
    board = StepBoard(V, F, thickness, (float(x1 - x0), float(y1 - y0)), float(V[:, 2].max()),
                      n_parts, str(path), flip)
    if cache is not None:
        meta = json.dumps({"thickness": board.thickness, "size": list(board.size),
                           "tallest": board.tallest, "n_parts": n_parts})
        np.savez_compressed(cache, V=V.astype(np.float32), F=F.astype(np.int32), meta=meta)
    return board


def registration(reg: dict) -> tuple[float, np.ndarray, float | None]:
    """Board -> plate: returns (angle_rad, translation, length_error_mm or None).
    plate = R(angle) * board + t."""
    b = np.asarray(reg.get("b") or [], float)
    p = np.asarray(reg.get("p") or [], float)
    n = min(len(b), len(p))
    if n == 0:
        raise ValueError("registration needs at least one board point and its plate point")
    if n == 1:
        th = math.radians(float(reg.get("rot_deg", 0)))
        R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        return th, p[0] - R @ b[0], None
    vb, vp = b[1] - b[0], p[1] - p[0]
    if np.linalg.norm(vp) < 1.0 or np.linalg.norm(vb) < 1.0:
        # Two points on the same spot carry no angle (atan2(0, 0) = 0, so the board was
        # silently placed at 0 deg and the rotation box ignored). Place from point 1 and
        # the rotation box instead, and still report the spacing error so it gets re-picked.
        th = math.radians(float(reg.get("rot_deg", 0)))
        R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
        return th, p[0] - R @ b[0], float(np.linalg.norm(vp) - np.linalg.norm(vb))
    th = math.atan2(vp[1], vp[0]) - math.atan2(vb[1], vb[0])
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    t = p[:2].mean(axis=0) - R @ b[:2].mean(axis=0)
    return th, t, float(np.linalg.norm(vp) - np.linalg.norm(vb))


def board_to_plate(V: np.ndarray, th: float, t: np.ndarray) -> np.ndarray:
    c, s = math.cos(th), math.sin(th)
    out = V.copy()
    out[:, 0] = c * V[:, 0] - s * V[:, 1] + t[0]
    out[:, 1] = s * V[:, 0] + c * V[:, 1] + t[1]
    return out


# ---- rasterizing ---------------------------------------------------------------------------

def raster_max(T: np.ndarray, x0: float, y0: float, nx: int, ny: int, res: float = RES) -> np.ndarray:
    """Highest Z of any triangle over each cell (conservative: every cell a triangle's
    bounding box touches gets the triangle's highest Z). Rows = Y, columns = X.
    Cells with nothing stay at -inf."""
    H = np.full((ny, nx), -np.inf)
    xmax, ymax = x0 + nx * res, y0 + ny * res
    lo, hi = T[:, :, :2].min(axis=1), T[:, :, :2].max(axis=1)
    keep = (hi[:, 0] >= x0) & (lo[:, 0] <= xmax) & (hi[:, 1] >= y0) & (lo[:, 1] <= ymax)
    work = T[keep]
    for _ in range(14):
        if not len(work):
            break
        lo, hi = work[:, :, :2].min(axis=1), work[:, :, :2].max(axis=1)
        span = (hi - lo).max(axis=1) / res
        small = span <= 2.0
        s = work[small]
        if len(s):
            z = s[:, :, 2].max(axis=1)
            i0 = np.floor((s[:, :, :2].min(axis=1) - (x0, y0)) / res).astype(np.int64)
            i1 = np.floor((s[:, :, :2].max(axis=1) - (x0, y0)) / res).astype(np.int64)
            for di in range(3):
                for dj in range(3):
                    cx, cy = i0[:, 0] + di, i0[:, 1] + dj
                    m = (cx <= i1[:, 0]) & (cy <= i1[:, 1]) & (cx >= 0) & (cy >= 0) & (cx < nx) & (cy < ny)
                    np.maximum.at(H, (cy[m], cx[m]), z[m])
        big = work[~small]
        if not len(big):
            break
        a, b, c = big[:, 0], big[:, 1], big[:, 2]
        ab, bc, ca = (a + b) / 2, (b + c) / 2, (c + a) / 2
        work = np.concatenate([np.stack(t, axis=1) for t in
                               ((a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca))])
    return H


def probe_structure(name: str, orientation: str, margin: float, res: float = RES,
                    tip_w: float | None = None):
    """Grey-dilation structure: minus the probe bottom height over its footprint."""
    spec = dict(PROBE_TIPS.get(name.upper(), PROBE_TIPS["H20"]))
    if tip_w:
        spec["tip_w"] = tip_w
    R0 = spec["tip_w"] / 2
    nu = int(math.ceil((R0 + margin) / res))
    nv = int(math.ceil((BLADE_T / 2 + margin) / res))
    u = np.arange(-nu, nu + 1) * res
    ue = np.maximum(np.abs(u) - margin, 0.0)
    if spec["ring"]:
        L = R0 - np.sqrt(np.maximum(R0 * R0 - np.minimum(ue, R0) ** 2, 0.0))
    else:
        L = np.zeros_like(u)
    S = -np.tile(L, (2 * nv + 1, 1))             # rows = v (across the blade), cols = u (along)
    return S if orientation.lower() == "x" else S.T


@dataclass
class Heights:
    """Height map and required tip height over a region of the plate (plate frame, mm,
    heights relative to the board top)."""
    x0: float
    y0: float
    res: float
    H: np.ndarray            # what is there
    req: np.ndarray          # lowest legal probe tip height
    sources: list
    notes: list

    @property
    def extent(self):
        ny, nx = self.H.shape
        return [self.x0, self.x0 + nx * self.res, self.y0, self.y0 + ny * self.res]

    def _cells(self, x, y):
        fx = (np.asarray(x, float) - self.x0) / self.res - 0.5
        fy = (np.asarray(y, float) - self.y0) / self.res - 0.5
        ix, iy = np.floor(fx).astype(int), np.floor(fy).astype(int)
        ny, nx = self.req.shape
        return ix, iy, nx, ny

    def req_at(self, x, y):
        """Highest required height among the 4 cells around (x, y)."""
        ix, iy, nx, ny = self._cells(x, y)
        out = None
        for dx in (0, 1):
            for dy in (0, 1):
                v = self.req[np.clip(iy + dy, 0, ny - 1), np.clip(ix + dx, 0, nx - 1)]
                out = v if out is None else np.maximum(out, v)
        return out

    def via(self, x0, y0, x1, y1) -> float:
        """Highest required tip height along the straight line between two points."""
        n = max(2, int(math.hypot(x1 - x0, y1 - y0) / (self.res / 2)) + 1)
        xs, ys = np.linspace(x0, x1, n), np.linspace(y0, y1, n)
        return float(np.max(self.req_at(xs, ys)))


def compute(cfg, setup: dict, area=None, board: StepBoard | None = None, log=print) -> Heights:
    """Build the height map over the scan area (plus the probe's reach) from the setup."""
    s = cfg["scan"]
    pr = cfg["probe"]
    margin = float(setup.get("margin_xy", 1.5))
    spec = dict(PROBE_TIPS.get(str(pr["name"]).upper(), PROBE_TIPS["H20"]))
    tip_w = float(pr.get("tip_w") or spec["tip_w"])
    head_h, head_r = float(pr.get("head_h", 20.0)), float(pr.get("head_r", 45.0))
    clearance = float(s["clearance"])
    ax0, ax1, ay0, ay1 = area or (s["x_min"], s["x_max"], s["y_min"], s["y_max"])
    reach = max(head_r, tip_w / 2 + margin) + 2 * RES
    x0, y0 = ax0 - reach, ay0 - reach
    nx = int(math.ceil((ax1 - ax0 + 2 * reach) / RES)) + 1
    ny = int(math.ceil((ay1 - ay0 + 2 * reach) / RES)) + 1
    H = np.zeros((ny, nx))                       # baseline: the board top plane
    sources, notes = [], []
    st = setup.get("step")
    if st and st.get("file"):
        from .config import resolve
        if board is None:
            board = load_step(resolve(cfg, st["file"]), bool(st.get("flip")),
                              cache_dir=Path(cfg["_dir"]))
        th, t, err = registration(st.get("reg") or {})
        Vp = board_to_plate(board.V, th, t)
        Hs = raster_max(Vp[board.F], x0, y0, nx, ny)
        H = np.maximum(H, Hs)
        sources.append(f"STEP {Path(board.source).name} ({board.n_parts} parts, tallest "
                       f"{board.tallest:.1f} mm, rotated {math.degrees(th):.1f} deg)")
        if err is not None and abs(err) > 1.0:
            notes.append(f"registration points disagree by {err:+.1f} mm: re-pick them")
    flat = float(setup.get("flat_mm") or 0)
    if flat > 0:
        H = np.maximum(H, flat)
        sources.append(f"master height {flat:g} mm")
    X = x0 + (np.arange(nx) + 0.5) * RES
    Y = y0 + (np.arange(ny) + 0.5) * RES
    for m in setup.get("marks") or []:
        r = float(m.get("r", 3.0)) + RES
        d2 = (X[None, :] - m["x"]) ** 2 + (Y[:, None] - m["y"]) ** 2
        H = np.where(d2 <= r * r, np.maximum(H, float(m["h"])), H)
    for b in setup.get("boxes") or []:
        mx = (X >= min(b["x0"], b["x1"]) - RES) & (X <= max(b["x0"], b["x1"]) + RES)
        my = (Y >= min(b["y0"], b["y1"]) - RES) & (Y <= max(b["y0"], b["y1"]) + RES)
        H = np.where(my[:, None] & mx[None, :], np.maximum(H, float(b["h"])), H)
    if setup.get("marks"):
        sources.append(f"{len(setup['marks'])} taught mark(s)")
    if setup.get("boxes"):
        sources.append(f"{len(setup['boxes'])} box(es)")

    from scipy import ndimage
    S = probe_structure(str(pr["name"]), str(pr["orientation"]), margin, RES, tip_w)
    ring = ndimage.grey_dilation(H, structure=S, mode="nearest")
    k = 4                                        # head check on a 2 mm grid
    pad_y, pad_x = (-ny) % k, (-nx) % k
    Hp = np.pad(H, ((0, pad_y), (0, pad_x)), mode="edge")
    coarse = Hp.reshape(Hp.shape[0] // k, k, Hp.shape[1] // k, k).max(axis=(1, 3))
    rr = int(math.ceil(head_r / (RES * k)))
    yy, xx = np.mgrid[-rr:rr + 1, -rr:rr + 1]
    disc = xx * xx + yy * yy <= rr * rr
    head = ndimage.maximum_filter(coarse, footprint=disc, mode="nearest") - head_h
    head = np.repeat(np.repeat(head, k, axis=0), k, axis=1)[:ny, :nx]
    req = np.maximum(ring, head) + clearance
    if abs(float(cfg["calibration"].get("angle_deg", 0))) > 5:
        notes.append("plate is turned more than 5 degrees on the bed: the probe footprint "
                     "is assumed square to the plate; re-seat the cover")
    return Heights(x0, y0, RES, H, req, sources, notes)


def compute_for(cfg, log=print) -> Heights | None:
    """Height map from the job's setup file, or None if the job has no setup."""
    setup = load_setup(cfg)
    if not uses_heights(setup):
        return None
    return compute(cfg, setup, log=log)
