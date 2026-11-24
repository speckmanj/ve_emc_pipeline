"""Gantry noise test (2026-11-24): is the JG Aurora A5 putting noise into the probe?

The probe stays in one spot over the EMPTY bed (no board). The script sweeps both bands
N times in each gantry state and compares them with the A5 switched off at the wall.

States, in order:
    hold_all    all stepper drivers on, nothing moving (this is how a scan sweeps)
    x_off       X driver off only        (M84 X)
    y_off       Y driver off only        (M84 Y)
    z_off       Z driver off only        (M84 Z)
    e_off       extruder driver off only (M84 E)
    all_off     every driver off         (M84)
    move_x      X running back and forth +/-15 mm during the sweeps
    move_y      Y running back and forth +/-15 mm during the sweeps
    wall_off    A5 switched off at the wall (you do this when asked; reference)

Moves: only the two motion states move, in X or Y only, at the current height. Z never moves.
Disabling drivers does not lose position unless the head is bumped, but re-home before
the next real scan anyway.

Usage (from the repo root):
    python tests/gantry_noise/gantry_noise.py <job.toml> [--sweeps 5] [--skip-wall]
Writes tests/gantry_noise/out/<stamp>/ : spectra.npz, summary.csv, overlay_b0.png, overlay_b1.png
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from emcscan import config  # noqa: E402
from emcscan.analyzers import make_analyzer  # noqa: E402
from emcscan.printers import make_printer  # noqa: E402

SUBBANDS = [(30, 40), (40, 88), (88, 108), (108, 174), (174, 300),
            (300, 470), (470, 800), (800, 1000)]
REF = "wall_off"


DROPS = []                                   # (state, time, error) for every TinySA dropout
WAITS_S = (0.0, 10.0, 30.0)                  # before each reconnect, like the scan app


def sweep_all(an, bands, n, state="?"):
    out = []
    for b in bands:
        rows, f = [], None
        while len(rows) < n:
            for k, wait in enumerate(WAITS_S + (None,)):
                try:
                    f, lv = an.sweep(b)
                    break
                except Exception as e:           # noqa: BLE001
                    DROPS.append((state, time.strftime("%H:%M:%S"), f"{type(e).__name__}: {str(e)[:150]}"))
                    print(f"  [tinysa] dropout in {state} ({type(e).__name__}: {str(e)[:100]})")
                    if wait is None:
                        raise
                    time.sleep(wait)
                    try:
                        an.reconnect()
                        print("  [tinysa] reconnected, repeating the sweep")
                    except Exception as e2:      # noqa: BLE001
                        print(f"  [tinysa] reconnect failed: {str(e2)[:100]}")
            rows.append(lv)
        out.append((f, np.array(rows)))
    return out


class Shuttle:
    """Keeps one axis running back and forth in a thread while the main thread sweeps.
    Marlin holds the 'ok' when its move buffer is full, so this paces itself."""
    def __init__(self, pr, axis, center, span, feed):
        self.pr, self.axis, self.c, self.s, self.feed = pr, axis, center, span, feed
        self.stop = threading.Event()
        self.t = threading.Thread(target=self.run, daemon=True)

    def run(self):
        k = 0
        while not self.stop.is_set():
            v = self.c + (self.s if k % 2 == 0 else -self.s)
            self.pr.send(f"G1 {self.axis}{v:.2f} F{self.feed:.0f}")
            k += 1

    def __enter__(self):
        self.t.start(); time.sleep(1.0); return self

    def __exit__(self, *exc):
        self.stop.set(); self.t.join(10)
        self.pr.send(f"G1 {self.axis}{self.c:.2f} F{self.feed:.0f}", wait=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--sweeps", type=int, default=5)
    ap.add_argument("--span", type=float, default=15.0)
    ap.add_argument("--skip-wall", action="store_true")
    ap.add_argument("--x", type=float, help="park X (printer mm), default bed center")
    ap.add_argument("--y", type=float, help="park Y (printer mm), default bed center")
    ap.add_argument("--z", type=float, help="park Z (printer mm), default safe_z")
    a = ap.parse_args()

    cfg = config.load(a.job)
    bands = cfg["analyzer"]["bands"]
    pc = cfg["printer"]
    out = Path(__file__).parent / "out" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)

    print("GANTRY NOISE TEST. Bed must be EMPTY (no board), probe mounted as for the scan.")
    print("The script homes the A5, then parks the probe (default: bed center at safe_z).")
    if input("Type GO to start: ").strip() != "GO":
        return

    an = make_analyzer(cfg); an.connect(); an.set_lna(True)
    pr = make_printer(cfg); pr.connect(); pr.prepare()
    # Opening the port resets the A5 (CH340 DTR), so Marlin forgets where it is and
    # reports X0 Y0 Z0. Home here and park at a known spot instead of trusting M114.
    px = a.x if a.x is not None else pc["bed_x"] / 2
    py = a.y if a.y is not None else pc["bed_y"] / 2
    pz = a.z if a.z is not None else float(pc["safe_z"])
    if input(f"Type HOME to home the A5 and park the probe at X{px:.0f} Y{py:.0f} Z{pz:.0f}: ").strip() != "HOME":
        return
    pr.home()
    pr.move_to(px, py, pz)
    pos = pr.where(strict=True) or (px, py, pz)
    x, y, z = pos
    span = a.span
    for axis, c, hi in (("X", x, pc["bed_x"]), ("Y", y, pc["bed_y"])):
        if c - span < 0 or c + span > hi:
            sys.exit(f"{axis}{c:.1f} is too close to the travel limit for +/-{span} mm; move it inward.")
    print(f"[test] probe at X{x:.1f} Y{y:.1f} Z{z:.1f}; {a.sweeps} sweeps per band per state")

    results = {}

    def run(name, setup=None):
        if setup:
            pr.send(setup, wait=True)
        time.sleep(1.0)
        t = time.time()
        results[name] = sweep_all(an, bands, a.sweeps, name)
        meds = [np.median(r) for _, r in results[name]]
        print(f"  {name:9s} done in {time.time() - t:4.1f} s   median "
              + "  ".join(f"{m:6.1f}" for m in meds) + " dBm")

    run("hold_all", "M17")
    run("x_off", "M17\nM84 X")
    run("y_off", "M17\nM84 Y")
    run("z_off", "M17\nM84 Z")
    run("e_off", "M17\nM84 E")
    run("all_off", "M84")
    pr.send("M17", wait=True)
    feed = float(pc["feed_xy"])
    with Shuttle(pr, "X", x, span, feed):
        run("move_x")
    with Shuttle(pr, "Y", y, span, feed):
        run("move_y")
    pr.send("M84 S120", wait=True)          # Marlin's usual idle timeout back
    pr.close()

    if not a.skip_wall:
        input("\nSwitch the A5 OFF at the wall now (leave the TinySA and probe as they are). "
              "Press Enter when it is off: ")
        try:
            results[REF] = sweep_all(an, bands, a.sweeps, REF)
            print(f"  {REF:9s} done")
        except Exception as e:                  # noqa: BLE001
            print(f"  {REF} failed: {e}")
    an.close()

    ref = REF if REF in results else "all_off"
    np.savez(out / "spectra.npz", **{f"{k}_b{i}_f": v[i][0] for k, v in results.items() for i in range(len(bands))},
             **{f"{k}_b{i}_lv": v[i][1] for k, v in results.items() for i in range(len(bands))})

    # Summary: median level per sub-band per state, and the difference from the reference.
    lines = ["state," + ",".join(f"{lo}-{hi}MHz" for lo, hi in SUBBANDS)]
    print(f"\nMedian level per sub-band, dB ABOVE '{ref}' (positive = gantry adds noise):")
    print(f"{'state':9s} " + " ".join(f"{f'{lo}-{hi}':>9s}" for lo, hi in SUBBANDS))

    def level(name, lo, hi):
        vals = []
        for f, r in results[name]:
            s = (f / 1e6 >= lo) & (f / 1e6 < hi)
            if s.any():
                vals.append(np.median(r[:, s], axis=0))
        return float(np.mean(np.concatenate(vals))) if vals else float("nan")

    for name in results:
        row = [level(name, lo, hi) - level(ref, lo, hi) for lo, hi in SUBBANDS]
        lines.append(name + "," + ",".join(f"{v:.1f}" for v in row))
        print(f"{name:9s} " + " ".join(f"{v:9.1f}" for v in row))
    (out / "summary.csv").write_text("\n".join(lines) + "\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for i, b in enumerate(bands):
        fig, ax = plt.subplots(figsize=(11, 4.5))
        for name, v in results.items():
            f, r = v[i]
            ax.plot(f / 1e6, np.median(r, axis=0), lw=1.0 if name != ref else 2.0,
                    color="k" if name == ref else None, label=name)
        ax.set_title(f"{b['name']}: median of {a.sweeps} sweeps per gantry state")
        ax.set_xlabel("MHz"); ax.set_ylabel("dBm"); ax.grid(alpha=0.3); ax.legend(fontsize=8, ncol=3)
        fig.tight_layout(); fig.savefig(out / f"overlay_b{i}.png", dpi=110); plt.close(fig)
    print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
