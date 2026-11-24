"""Report: turn a run folder into pictures, tables and one self-contained HTML page.

    emcscan report runs/<run>

Writes runs/<run>/report/:
    maxhold_b<k>.png        strongest level seen anywhere, per frequency (what is there)
    map_h<z>_b<k>.png       peak level per point at each height, over the board photo
    freqmap_h<z>_b<k>_<f>.png   level at ONE frequency (where a specific peak comes from)
    view3d.png              all heights stacked in 3D
    hotspots.csv            strongest spots: position, height, level, frequency
    scan.npz                all arrays for your own analysis (numpy.load)
    report.html             everything above in one file (open in a browser, print to PDF)

Units: levels are reported in dBµV at the probe output by default (dBm + 107 in 50 ohm),
the unit EMC labs use. It is still a relative near-field level, NOT dBµV/m field strength,
so it never goes against an FCC/CISPR limit line. Raw run data stay in dBm (what the
analyzer returns); [report] unit = "dBm" in the job file, or --unit dBm, reports in dBm.

How the numbers are made:
    peak level per point  = max over the band of that point's sweep
    max-hold spectrum     = max over all points, per frequency bin
    noise floor           = median of all sweeps (robust to the peaks)
    top frequencies       = max-hold peaks at least 6 dB over the floor, 5 bins apart
    hotspots              = local maxima of the peak map (strongest within 6 mm), 10 dB over the floor
"""
from __future__ import annotations

import base64
import csv
import html
import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                         # noqa: E402
import numpy as np                                      # noqa: E402

from .runstore import Run                               # noqa: E402

UNITS = {"dbuv": ("dBuV", 107.0, "dBµV"), "dbm": ("dBm", 0.0, "dBm")}   # key, offset from dBm, label


def resolve_unit(run_dir, unit: str | None = None) -> tuple[str, float, str]:
    """(key, offset added to dBm, label). unit None: [report] unit from the run's config.toml,
    else the default (dBµV)."""
    if unit is None:
        try:
            from . import config
            unit = config.load(Path(run_dir) / "config.toml")["report"]["unit"]
        except Exception:                                   # noqa: BLE001
            unit = "dBuV"
    k = str(unit).lower().replace("µ", "u").replace("μ", "u")
    if k not in UNITS:
        raise ValueError(f"unknown report unit {unit!r}: use dBuV or dBm")
    return UNITS[k]


def apply_unit(run: Run, offset: float) -> None:
    """Shift the run's levels in place (the raw files are untouched)."""
    if offset:
        run.traces = [t + np.float32(offset) for t in run.traces]


def unit_note(label: str) -> str:
    if label == "dBm":
        return "Levels in dBm at the probe output (50 ohm)."
    return ("Levels in dBµV at the probe output (dBm + 107 in 50 ohm). These are relative "
            "near-field levels for locating sources and comparing scans; they are not "
            "field strength (dBµV/m) and cannot be compared with a radiated-emission limit.")


DISCLAIMER = ("Near-field confidence scan. This shows where emissions come from and how "
              "they change after a fix. It is not a compliance measurement and does not "
              "predict a pass or fail at an accredited test site.")


def top_freqs(freqs, maxhold, floor, n=3, min_db=6.0, sep=5):
    order = np.argsort(maxhold)[::-1]
    picked = []
    for i in order:
        if maxhold[i] < floor + min_db or len(picked) >= n:
            break
        if all(abs(i - j) >= sep for j in picked):
            picked.append(int(i))
    return [(float(freqs[i]), float(maxhold[i])) for i in picked]


def hotspots(run: Run, k: int, n=5, radius_mm=6.0, min_db=10.0):
    """Separate sources: points that are the strongest within radius_mm around them
    (a local maximum of the peak map) and at least min_db over the noise floor."""
    tr = run.traces[k]
    pf = run.freqs[k][tr.argmax(axis=1)]
    floor = float(np.median(tr))
    pg = run.peak_grid(k)
    step = float(np.min(np.diff(run.xs))) if len(run.xs) > 1 else 1.0
    r = max(1, int(round(radius_mm / step)))
    pl = run.plan
    row_of = {(int(pl["iz"][i]), int(pl["iy"][i]), int(pl["ix"][i])): j
              for j, i in enumerate(run.done_idx)}
    cands = []
    nz, ny, nx = pg.shape
    for iz in range(nz):
        g = pg[iz]
        for iy in range(ny):
            for ix in range(nx):
                v = g[iy, ix]
                if np.isnan(v) or v < floor + min_db:
                    continue
                win = g[max(iy - r, 0):iy + r + 1, max(ix - r, 0):ix + r + 1]
                if v >= np.nanmax(win):
                    j = row_of[(iz, iy, ix)]
                    cands.append({"band": run.bands[k]["name"], "x": float(run.xs[ix]),
                                  "y": float(run.ys[iy]), "h": float(run.hs[iz]),
                                  "level": float(v), "freq_mhz": float(pf[j]) / 1e6, "row": j})
    cands.sort(key=lambda s: -s["level"])
    return cands[:n]


def raised_grid(run: Run) -> np.ndarray | None:
    """[height, row, col] tip height where a point was raised over a tall part, else NaN."""
    names = run.plan.dtype.names or ()
    if "raised" not in names:
        return None
    pl = run.plan
    g = np.full((len(run.hs), len(run.ys), len(run.xs)), np.nan)
    m = pl["raised"] > 0
    g[pl["iz"][m].astype(int), pl["iy"][m].astype(int), pl["ix"][m].astype(int)] = pl["ht"][m]
    return g if m.any() else None


def _map_axes(run: Run, ax, grid2d, title, vmin, vmax, spots=None, cmap="inferno", label="dBm",
              iz=None):
    step = float(np.min(np.diff(run.xs))) if len(run.xs) > 1 else 1.0
    ext = [run.xs[0] - step / 2, run.xs[-1] + step / 2, run.ys[0] - step / 2, run.ys[-1] + step / 2]
    if run.photo is not None:
        ax.imshow(run.photo, extent=run.photo_extent)
        alpha = 0.6
    else:
        alpha = 1.0
    im = ax.imshow(grid2d, origin="lower", extent=ext, cmap=cmap, vmin=vmin, vmax=vmax,
                   alpha=alpha, interpolation="bilinear")
    rg = raised_grid(run) if iz is not None else None
    if rg is not None and np.isfinite(rg[iz]).any():
        X, Y = np.meshgrid(run.xs, run.ys)
        m = np.isfinite(rg[iz])
        ax.scatter(X[m], Y[m], s=9, marker="x", c="white", linewidths=0.7, alpha=0.8,
                   label="raised over a tall part (reads weaker)")
        ax.legend(loc="upper right", fontsize=7, framealpha=0.7)
    if spots:
        for n, s in enumerate(spots, 1):
            ax.plot(s["x"], s["y"], "o", mfc="none", mec="cyan", ms=14, mew=1.5)
            ax.text(s["x"], s["y"], str(n), color="cyan", ha="center", va="center", fontsize=9,
                    fontweight="bold")
    pad = 4 * step
    ax.set_xlim(ext[0] - pad, ext[1] + pad); ax.set_ylim(ext[2] - pad, ext[3] + pad)
    ax.set_xlabel("X plate (mm)"); ax.set_ylabel("Y plate (mm)")
    ax.set_title(title, fontsize=10)
    plt.colorbar(im, ax=ax, label=label, shrink=0.85)


def _png(fig, path: Path) -> str:
    fig.savefig(path, dpi=130, bbox_inches="tight")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def view3d(run: Run, path: Path, label: str = "dBm") -> str | None:
    try:
        import pyvista as pv
    except ImportError:
        return None
    from .runstore import crop_photo, photo_texture_plane
    pl = pv.Plotter(off_screen=True, window_size=(1100, 800))
    step = float(np.min(np.diff(run.xs))) if len(run.xs) > 1 else 1.0
    box = (run.xs[0] - 10, run.xs[-1] + 10, run.ys[0] - 10, run.ys[-1] + 10)
    if run.photo is not None:
        img, ext = crop_photo(run.photo, run.photo_extent, box)
        plane, tex = photo_texture_plane(img, ext)
        pl.add_mesh(plane, texture=tex)
    allpk = np.max([run.peak_grid(k) for k in range(len(run.bands))], axis=0)
    vmin, vmax = np.nanmin(allpk), np.nanmax(allpk)
    for iz, h in enumerate(run.hs):
        X, Y = np.meshgrid(run.xs, run.ys)
        grid = pv.StructuredGrid(X, Y, np.full_like(X, h, dtype=float))
        grid["level"] = np.nan_to_num(allpk[iz], nan=vmin).ravel(order="F")
        pl.add_mesh(grid, scalars="level", cmap="inferno", clim=[vmin, vmax], opacity=0.85,
                    scalar_bar_args={"title": f"peak {label}"})
    pl.show_grid(xtitle="X (mm)", ytitle="Y (mm)", ztitle="h (mm)", n_zlabels=3, fmt="%.0f")
    from .runstore import front_view
    front_view(pl, box, float(run.hs.max()))
    pl.show(screenshot=str(path), auto_close=True)
    return base64.b64encode(path.read_bytes()).decode()


def make_report(run_dir: str | Path, unit: str | None = None) -> Path:
    run = Run(run_dir)
    if len(run.done_idx) == 0:
        raise RuntimeError("no measured points in this run yet")
    ukey, uoff, U = resolve_unit(run.dir, unit)
    apply_unit(run, uoff)
    out = run.dir / "report"; out.mkdir(exist_ok=True)
    meta = run.meta
    sections = []
    all_spots = []
    npz = {"x": run.xs, "y": run.ys, "h": run.hs, "done_idx": run.done_idx, "unit": U}

    for k, band in enumerate(run.bands):
        fr, tr = run.freqs[k], run.traces[k]
        maxhold = tr.max(axis=0)
        floor = float(np.median(tr))
        tops = top_freqs(fr, maxhold, floor)
        spots = hotspots(run, k)
        all_spots += spots
        npz[f"freqs_b{k}"] = fr; npz[f"traces_b{k}"] = tr
        npz[f"peak_grid_b{k}"] = run.peak_grid(k)

        # max-hold spectrum
        fig, ax = plt.subplots(figsize=(10, 3.6))
        ax.plot(fr / 1e6, maxhold, lw=0.9, label="max-hold (all points)")
        ax.axhline(floor, color="gray", ls="--", lw=0.8, label=f"noise floor {floor:.1f} {U}")
        for f, lv in tops:
            ax.annotate(f"{f / 1e6:.2f} MHz", (f / 1e6, lv), textcoords="offset points",
                        xytext=(0, 6), ha="center", fontsize=8)
        ax.set_xlabel("MHz"); ax.set_ylabel(U); ax.grid(alpha=0.3); ax.legend(fontsize=8)
        ax.set_title(f"{band['name']}: max-hold spectrum")
        imgs = [("Max-hold spectrum", _png(fig, out / f"maxhold_b{k}.png"))]

        # peak maps per height (shared color scale within the band)
        pg = run.peak_grid(k)
        vmin, vmax = np.nanmin(pg), np.nanmax(pg)
        for iz, h in enumerate(run.hs):
            fig, ax = plt.subplots(figsize=(7, 6))
            sp = [s for s in spots if s["h"] == h]
            _map_axes(run, ax, pg[iz], f"{band['name']}  peak level  h = {h:g} mm", vmin, vmax, sp,
                      iz=iz, label=U)
            imgs.append((f"Peak level, h = {h:g} mm", _png(fig, out / f"map_h{h:g}_b{k}.png")))
        # one map per top frequency, lowest height
        for f, _ in tops:
            g = run.freq_grid(k, f)
            fig, ax = plt.subplots(figsize=(7, 6))
            _map_axes(run, ax, g[0], f"{f / 1e6:.2f} MHz  h = {run.hs[0]:g} mm",
                      np.nanmin(g), np.nanmax(g), iz=0, label=U)
            imgs.append((f"Where {f / 1e6:.2f} MHz comes from",
                         _png(fig, out / f"freqmap_h{run.hs[0]:g}_b{k}_{f / 1e6:.0f}MHz.png")))
        # spectra at the hotspots
        fig, ax = plt.subplots(figsize=(10, 3.6))
        for n, s in enumerate(spots[:3], 1):
            ax.plot(fr / 1e6, tr[s["row"]], lw=0.8,
                    label=f"#{n} ({s['x']:.0f}, {s['y']:.0f}) h {s['h']:g}")
        ax.axhline(floor, color="gray", ls="--", lw=0.8)
        ax.set_xlabel("MHz"); ax.set_ylabel(U); ax.grid(alpha=0.3)
        if spots:
            ax.legend(fontsize=8)
        ax.set_title(f"{band['name']}: spectra at the strongest spots"
                     + ("" if spots else " (none 10 dB over the floor)"))
        imgs.append(("Spectra at hotspots", _png(fig, out / f"hotspot_spectra_b{k}.png")))
        sections.append((band, floor, tops, spots, imgs))

    np.savez_compressed(out / "scan.npz", **npz)
    with open(out / "hotspots.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["band", "x", "y", "h", f"level_{ukey.lower()}", "freq_mhz"])
        w.writeheader()
        for s in all_spots:
            w.writerow({(f"level_{ukey.lower()}" if k == "level" else k):
                        (round(v, 2) if isinstance(v, float) else v)
                        for k, v in s.items() if k != "row"})
    v3d = view3d(run, out / "view3d.png", U)

    # ---- HTML ----
    n_over = sum(int(r["overload"]) for r in run.points)
    esc = html.escape
    rows = "".join(f"<tr><th>{esc(k)}</th><td>{esc(str(v))}</td></tr>" for k, v in [
        ("Job", meta.get("job")), ("Started", meta.get("started")), ("Ended", meta.get("ended", "")),
        ("State", meta.get("state", "")),
        ("Points measured", f"{len(run.done_idx)} of {meta.get('n_points')}"),
        ("Grid step", f"{meta.get('step')} mm"), ("Heights", f"{meta.get('heights')} mm above board"),
        ("Probe", f"{meta.get('probe', {}).get('name')} ({meta.get('probe', {}).get('orientation')}"
                  f" orientation)"),
        ("Analyzer", meta.get("analyzer")), ("LNA", "on" if meta.get("lna_on") else "off"),
        ("Height sources", "; ".join(meta.get("height_sources") or []) or "one flat height"),
        ("Points raised over tall parts", meta.get("n_raised", 0)),
        ("Points flagged possible overload", n_over)])
    body = [f"<h1>EMC near-field scan: {esc(str(meta.get('job')))}</h1>",
            f"<p class='note'>{esc(DISCLAIMER)}</p>", f"<p class='note'>{esc(unit_note(U))}</p>",
            f"<table class='meta'>{rows}</table>"]
    if n_over:
        body.append("<p class='warn'>Some points read above the overload level with the LNA on. "
                    "Rescan with analyzer.lna = \"off\" to confirm those levels.</p>")
    if meta.get("n_raised"):
        body.append("<p class='note'>Points marked x were measured higher than the nominal "
                    "height to clear a tall part, so they read weaker than their neighbours. "
                    "A quiet area under an x is not proof of a quiet area.</p>")
        hz = run.dir / "heights.npz"
        if hz.exists():
            z = np.load(hz)
            fig, ax = plt.subplots(figsize=(7, 5))
            ext = list(z["extent"])
            if run.photo is not None:
                ax.imshow(run.photo, extent=run.photo_extent)
            im = ax.imshow(z["req"], origin="lower", extent=ext, cmap="viridis", alpha=0.65)
            ax.set_xlim(run.xs[0] - 5, run.xs[-1] + 5); ax.set_ylim(run.ys[0] - 5, run.ys[-1] + 5)
            ax.set_title("Lowest legal probe tip height (mm above board)", fontsize=10)
            ax.set_xlabel("X plate (mm)"); ax.set_ylabel("Y plate (mm)")
            plt.colorbar(im, ax=ax, shrink=0.85, label="mm")
            body.append(f"<h2>Probe height map</h2><img src='data:image/png;base64,"
                        f"{_png(fig, out / 'height_map.png')}'>")
    if v3d:
        body.append(f"<h2>3D overview</h2><img src='data:image/png;base64,{v3d}'>")
    for band, floor, tops, spots, imgs in sections:
        body.append(f"<h2>{esc(band['name'])}</h2><p>Noise floor {floor:.1f} {U}. Strongest "
                    "frequencies: " + (", ".join(f"{f / 1e6:.2f} MHz ({lv:.1f} {U})" for f, lv in tops)
                                       or "none above floor + 6 dB") + "</p>")
        body.append("<table><tr><th>#</th><th>X mm</th><th>Y mm</th><th>h mm</th>"
                    f"<th>Level {U}</th><th>Freq MHz</th></tr>" + "".join(
                        f"<tr><td>{n}</td><td>{s['x']:.1f}</td><td>{s['y']:.1f}</td>"
                        f"<td>{s['h']:g}</td><td>{s['level']:.1f}</td><td>{s['freq_mhz']:.2f}</td></tr>"
                        for n, s in enumerate(spots, 1)) + "</table>")
        for title, b64 in imgs:
            body.append(f"<h3>{esc(title)}</h3><img src='data:image/png;base64,{b64}'>")
    css = ("body{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:24px auto;color:#222}"
           "img{max-width:100%;border:1px solid #ddd;margin:6px 0}"
           "table{border-collapse:collapse;margin:8px 0}td,th{border:1px solid #ccc;padding:3px 8px;"
           "text-align:left;font-size:13px}.note{background:#f4f6f8;padding:8px;border-left:4px solid #607d8b}"
           ".warn{background:#fff3e0;padding:8px;border-left:4px solid #ef6c00}")
    page = (f"<!doctype html><html><head><meta charset='utf-8'><title>EMC scan {esc(str(meta.get('job')))}"
            f"</title><style>{css}</style></head><body>{''.join(body)}</body></html>")
    (out / "report.html").write_text(page, encoding="utf-8")
    print(f"[report] {out / 'report.html'}")
    return out / "report.html"
