"""Quiet-sweep drift check (2026-11-24): do the A5's axes come back to the same spot after
the stepper drivers are switched off for a sweep and back on?

Flow:
  1. Home, park over the bed (default bed center, Z20).
  2. You jog the probe tip down until it JUST touches a fine pen dot on paper on the bed.
  3. It lifts 1 mm and runs N scan-like cycles: move 2 mm out (X and Y) and back,
     settle, drivers OFF (M84), sweep the first band with the TinySA, drivers ON (M17).
     The first --held cycles sweep with the drivers held instead, to compare noise.
     TinySA dropouts are recovered exactly as in a real scan (emcscan.scan.sweep_point).
  4. It returns over the dot, waits for Enter, then lowers 1 mm. Look: still on the dot?

Usage (repo root): python tests/gantry_noise/drift_check.py <job.toml> [--cycles 100] [--held 10]
Writes tests/gantry_noise/out/drift_<stamp>/ : cycles.csv, noise.png
"""
from __future__ import annotations

import argparse
import copy
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from emcscan import config  # noqa: E402
from emcscan.analyzers import make_analyzer  # noqa: E402
from emcscan.printers import make_printer  # noqa: E402
from emcscan.scan import sweep_point  # noqa: E402

JOG_HELP = ("  x 0.5 | y -0.2 | z -0.1   jog (mm)\n"
            "  go                         tip is touching the dot: start the cycles\n"
            "  q                          lift and quit")


JOG_RE = __import__("re").compile(r"^\s*([xyz])\s*([-+]?\d*\.?\d+)\s*(mm)?\s*$")


def jog_to_dot(pr, z_min):
    print("\nJog the probe tip down onto the pen dot (paper just touching, no bending).")
    print("Use these jogs, NOT the printer's touchscreen (it may share the serial line).")
    print(JOG_HELP)
    while True:
        raw = input("jog> ").strip().lower()
        if not raw:
            continue
        if raw == "go":
            return True
        if raw == "q":
            return False
        m = JOG_RE.match(raw)                    # "z -1", "z-1", "z -0.1mm", "x+2"
        if not m:
            print(JOG_HELP); continue
        axis, d = m.group(1), float(m.group(2))
        if abs(d) > 20:
            print("  max 20 mm per jog"); continue
        try:
            x, y, z = pr.where(strict=True)
            if axis == "z" and z + d < z_min:
                print(f"  refused: Z would go below z_min {z_min}"); continue
            pr.jog(**{f"d{axis}": d})
            x, y, z = pr.where(strict=True)
            print(f"  at X{x:.2f} Y{y:.2f} Z{z:.2f}")
        except Exception as e:                   # noqa: BLE001
            print(f"  jog failed, nothing else sent: {type(e).__name__}: {str(e)[:200]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--cycles", type=int, default=100)
    ap.add_argument("--held", type=int, default=10, help="first cycles with drivers held (noise reference)")
    ap.add_argument("--step", type=float, default=2.0, help="out-and-back move, mm")
    ap.add_argument("--x", type=float); ap.add_argument("--y", type=float)
    ap.add_argument("--z", type=float, default=20.0)
    a = ap.parse_args()

    cfg = config.load(a.job)
    pc = cfg["printer"]
    cfg1 = copy.deepcopy(cfg)
    cfg1["analyzer"]["bands"] = cfg["analyzer"]["bands"][:1]     # the noisy band only, faster
    band_name = cfg1["analyzer"]["bands"][0]["name"]
    out = Path(__file__).parent / "out" / ("drift_" + time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True)

    print("QUIET-SWEEP DRIFT CHECK. Paper with a fine pen dot taped flat on the bed; no board.")
    if input("Type GO to start: ").strip() != "GO":
        return
    an = make_analyzer(cfg); an.connect(); an.set_lna(True)
    pr = make_printer(cfg); pr.connect(); pr.prepare()
    px = a.x if a.x is not None else pc["bed_x"] / 2
    py = a.y if a.y is not None else pc["bed_y"] / 2
    if input(f"Type HOME to home the A5 and park at X{px:.0f} Y{py:.0f} Z{a.z:.0f}: ").strip() != "HOME":
        pr.close(); an.close(); return
    pr.home()
    pr.move_to(px, py, a.z)

    if not jog_to_dot(pr, float(pc["z_min"])):
        pr.lift(); pr.send("M84 S120", wait=True); pr.close(); an.close(); return
    x0, y0, z0 = pr.where(strict=True)
    zc = z0 + 1.0
    print(f"[drift] dot at X{x0:.2f} Y{y0:.2f} Z{z0:.2f}; cycling at Z{zc:.2f}")
    pr.move_to(x0, y0, zc)

    rec = {"events": []}
    rows, held_lv, quiet_lv, f = [], [], [], None
    t0 = time.time()
    try:
        for i in range(a.cycles):
            quiet = i >= a.held
            pr.move_to(x0 + a.step, y0 + a.step, zc)
            pr.move_to(x0, y0, zc)
            if quiet:
                pr.quiet()
            try:
                p = SimpleNamespace(x=x0, y=y0, ht=1.0, idx=i)
                f, lv = sweep_point(cfg1, an, p, rec)[0]
            finally:
                if quiet:
                    pr.wake()
            (quiet_lv if quiet else held_lv).append(lv)
            pos = pr.where(strict=True)
            rows.append((i, "quiet" if quiet else "held", *pos, float(np.median(lv))))
            if i % 10 == 9 or i == a.cycles - 1:
                el = time.time() - t0
                print(f"  cycle {i + 1:3d}/{a.cycles}  {el / (i + 1):4.1f} s/cycle  "
                      f"M114 X{pos[0]:.2f} Y{pos[1]:.2f} Z{pos[2]:.2f}  "
                      f"TinySA recoveries {len(rec['events'])}")
    except KeyboardInterrupt:
        print("\n[drift] stopped by Ctrl+C")

    pr.move_to(x0, y0, zc)
    input("\nBack over the dot, 1 mm up. Press Enter to lower 1 mm onto the paper: ")
    pr.move_to(x0, y0, z0)
    input("Look closely: is the tip still on the dot? Note any offset and its direction. "
          "Press Enter to lift and finish: ")
    pr.lift(); pr.send("M84 S120", wait=True); pr.close(); an.close()

    (out / "cycles.csv").write_text("cycle,mode,x,y,z,median_dbm\n" + "\n".join(
        f"{r[0]},{r[1]},{r[2]:.2f},{r[3]:.2f},{r[4]:.2f},{r[5]:.1f}" for r in rows) + "\n")
    if rec["events"]:
        (out / "dropouts.txt").write_text("\n".join(f"{e['t']}  cycle {e['idx']}  ok={e['ok']}  {e['error']}"
                                                    for e in rec["events"]) + "\n")
    print(f"\nTinySA recoveries: {len(rec['events'])}")

    if held_lv and quiet_lv and f is not None:
        h, q = np.median(np.array(held_lv), 0), np.median(np.array(quiet_lv), 0)
        print(f"{band_name}: quiet sweeps vs held, dB lower (positive = quieter):")
        for lo, hi in ((30, 40), (40, 88), (88, 108), (108, 174), (174, 300)):
            s = (f / 1e6 >= lo) & (f / 1e6 < hi)
            if s.any():
                print(f"  {lo:3d}-{hi:3d} MHz  {float(np.mean(h[s] - q[s])):5.1f} dB")
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(11, 4.5))
        ax.plot(f / 1e6, h, lw=1, label=f"drivers held ({len(held_lv)} sweeps, median)")
        ax.plot(f / 1e6, q, lw=1, label=f"quiet sweep ({len(quiet_lv)} sweeps, median)")
        ax.set_title(f"{band_name}: held vs quiet sweep at the dot")
        ax.set_xlabel("MHz"); ax.set_ylabel("dBm"); ax.grid(alpha=0.3); ax.legend()
        fig.tight_layout(); fig.savefig(out / "noise.png", dpi=110); plt.close(fig)
    print(f"Saved to {out}")


if __name__ == "__main__":
    main()
