"""Before / after comparison of two runs on the same grid.

    emcscan compare runs/<before> runs/<after>

Typical uses: board before and after a fix; board powered vs a background (unpowered)
scan. Writes runs/<after>/compare_<before>/compare.html with, per band and height:
    before map, after map, and the difference (after - before; blue = quieter).
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt                      # noqa: E402
import numpy as np                                   # noqa: E402

from .report import DISCLAIMER, _map_axes, _png      # noqa: E402
from .runstore import Run                            # noqa: E402


def compare(dir_a: str | Path, dir_b: str | Path) -> Path:
    a, b = Run(dir_a), Run(dir_b)
    same = (len(a.xs) == len(b.xs) and len(a.ys) == len(b.ys) and len(a.hs) == len(b.hs)
            and np.allclose(a.xs, b.xs) and np.allclose(a.ys, b.ys) and np.allclose(a.hs, b.hs)
            and len(a.bands) == len(b.bands))
    if not same:
        raise RuntimeError("the two runs do not use the same grid, heights and bands; "
                           "rescan with the same [scan] and [analyzer] settings")
    out = b.dir / f"compare_{a.dir.name}"; out.mkdir(exist_ok=True)
    esc = html.escape
    body = [f"<h1>Before / after</h1><p>Before: {esc(a.dir.name)}<br>After: {esc(b.dir.name)}</p>",
            f"<p class='note'>{esc(DISCLAIMER)}</p>"]
    for k, band in enumerate(a.bands):
        ga, gb = a.peak_grid(k), b.peak_grid(k)
        d = gb - ga
        vmin = np.nanmin([np.nanmin(ga), np.nanmin(gb)]); vmax = np.nanmax([np.nanmax(ga), np.nanmax(gb)])
        lim = max(np.nanmax(np.abs(d)), 1.0)
        mh_a, mh_b = a.traces[k].max(axis=0), b.traces[k].max(axis=0)
        body.append(f"<h2>{esc(band['name'])}</h2><p>Strongest point: before {np.nanmax(ga):.1f} dBm, "
                    f"after {np.nanmax(gb):.1f} dBm ({np.nanmax(gb) - np.nanmax(ga):+.1f} dB). "
                    f"Largest drop at one point {np.nanmin(d):+.1f} dB, largest rise {np.nanmax(d):+.1f} dB.</p>")
        fig, ax = plt.subplots(figsize=(10, 3.6))
        ax.plot(a.freqs[k] / 1e6, mh_a, lw=0.8, label="before (max-hold)")
        ax.plot(b.freqs[k] / 1e6, mh_b, lw=0.8, label="after (max-hold)")
        ax.set_xlabel("MHz"); ax.set_ylabel("dBm"); ax.grid(alpha=0.3); ax.legend(fontsize=8)
        body.append(f"<img src='data:image/png;base64,{_png(fig, out / f'maxhold_b{k}.png')}'>")
        for iz, h in enumerate(a.hs):
            fig, axs = plt.subplots(1, 3, figsize=(18, 5.5))
            _map_axes(a, axs[0], ga[iz], f"before  h = {h:g} mm", vmin, vmax)
            _map_axes(b, axs[1], gb[iz], f"after  h = {h:g} mm", vmin, vmax)
            _map_axes(b, axs[2], d[iz], "after - before (blue = quieter)", -lim, lim,
                      cmap="RdBu_r", label="dB")
            body.append(f"<img src='data:image/png;base64,{_png(fig, out / f'compare_h{h:g}_b{k}.png')}'>")
    css = ("body{font-family:Segoe UI,Arial,sans-serif;max-width:1200px;margin:24px auto}"
           "img{max-width:100%;border:1px solid #ddd}.note{background:#f4f6f8;padding:8px;"
           "border-left:4px solid #607d8b}")
    (out / "compare.html").write_text(f"<!doctype html><html><head><meta charset='utf-8'>"
                                      f"<style>{css}</style></head><body>{''.join(body)}</body></html>",
                                      encoding="utf-8")
    print(f"[compare] {out / 'compare.html'}")
    return out / "compare.html"
