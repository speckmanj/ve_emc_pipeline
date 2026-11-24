"""Bench helpers: test the devices, home, calibrate the plate position, park the probe.

Calibration answers three questions, saved into the job's [calibration] section:
    origin_x, origin_y   where is marker 0, in nozzle coordinates?   (commands: first, second)
    angle_deg            is the plate turned on the bed?
The tip visits the back pair (markers 3 then 2; the front ones are out of the probe's
reach on the A5) and marker 0 is worked out from the plate layout. `save` writes the plate
cal to the job and to the machine template, so every new job starts with it.
    z_plate_top          at what nozzle Z does the nozzle touch the plate top? (command: ztop)
"""
from __future__ import annotations

import math
import time
from pathlib import Path

import numpy as np

from . import config as cfgmod
from . import geometry
from .analyzers import make_analyzer
from .printers import make_printer


def printer_test(cfg):
    pr = make_printer(cfg)
    pr.connect()
    pr.prepare()
    if hasattr(pr, "status"):
        st = pr.status()
        print({k: st.get(k) for k in ("gcode_state", "nozzle_temper", "bed_temper", "mc_print_stage")})
    print("Connected; heaters set to 0. No motion was commanded.")
    pr.close()


def analyzer_test(cfg):
    an = make_analyzer(cfg)
    an.connect()
    for lna in (False, True):
        an.set_lna(lna)
        for band in cfg["analyzer"]["bands"]:
            t = time.time()
            f, lv = an.sweep(band, (0, 0, 10))
            i = int(np.argmax(lv))
            print(f"LNA {'on ' if lna else 'off'} {band['name']:>14s}: {len(lv)} pts in {time.time() - t:4.2f} s, "
                  f"median {np.median(lv):6.1f} dBm, peak {lv[i]:6.1f} dBm @ {f[i] / 1e6:8.2f} MHz")
    an.close()


def home(cfg):
    print("HOMING. The A1 mini finds Z by pressing the nozzle onto the bed.\n"
          "  - The bed must be EMPTY: no plate, no board, no tape.\n"
          "  - The probe tip must sit ABOVE the nozzle tip (or be unclipped).")
    if input("Type HOME to continue: ").strip() != "HOME":
        print("Cancelled.")
        return
    pr = make_printer(cfg)
    pr.connect(); pr.prepare(); pr.home()
    sz = float(cfg["printer"]["safe_z"])
    pr.move_to(cfg["printer"]["bed_x"] / 2, cfg["printer"]["bed_y"] / 2, sz)
    print(f"Homed, parked at Z {sz}. Now place the plate and board; do NOT home again until done.")
    pr.close()


def goto(cfg, x, y, h):
    """Park the probe over a plate point (e.g. a hotspot) to watch the TinySA by hand."""
    if h < geometry.min_height(cfg):
        raise SystemExit(f"h {h} is below the safe minimum {geometry.min_height(cfg):.1f} mm")
    pr = make_printer(cfg)
    pr.connect(); pr.prepare()
    nx, ny, nz = geometry.plate_to_nozzle(cfg, x, y, h)
    pr.move_to(nx, ny, float(cfg["printer"]["safe_z"]))
    pr.move_to(nx, ny, nz)
    input(f"Probe over plate ({x}, {y}) at h {h} mm (nozzle {nx:.1f}, {ny:.1f}, {nz:.1f}). "
          "Press Enter to lift and finish.")
    pr.lift(); pr.close()


HELP = """Commands (distances in mm, relative jogs):
  x 1.5 | y -2 | z -0.5   jog the nozzle
  m 0..3                  go to marker n (safe height, then down to align height)
  first                   probe tip is over marker 3 center (back-left, 4 dimples)
  second                  probe tip is over marker 2 center (back-right, 3 dimples):
                          works out marker 0 and the plate rotation
  ztop                    nozzle just touches a paper strip on the plate top: save Z
  show                    print the current calibration
  save                    write calibration into the job file
  q                       lift and quit
"""


def calibrate(cfg):
    print("CALIBRATE. The printer must already be homed (emcscan home) with the plate and\n"
          "board placed afterwards. Look from the front and the side when aligning.\n")
    if input("Printer homed since power-on and plate placed after homing? [y/N] ").lower() != "y":
        print("Run `emcscan home` first.")
        return
    cal = dict(cfg["calibration"])
    cal.setdefault("z_plate_top", cfg["plate"]["height"])
    pr = make_printer(cfg)
    pr.connect(); pr.prepare()
    safe = float(cfg["printer"]["safe_z"])
    centers = geometry.marker_centers_plate(cfg)

    def align_z():
        return float(cal["z_plate_top"]) + max(2.0 - cfg["probe"]["offset_z"], 1.0)

    def marker_xy(i):
        a = math.radians(cal["angle_deg"])
        px, py = centers[i]
        return (cal["origin_x"] + math.cos(a) * px - math.sin(a) * py,
                cal["origin_y"] + math.sin(a) * px + math.cos(a) * py)

    pair = geometry.cal_pair(cfg)
    first = None
    x, y = marker_xy(pair[0])
    pr.move_to(x, y, safe)
    z = align_z()
    pr.move_to(x, y, z)
    print(HELP)
    try:
        while True:
            cmd = input(f"[nozzle {x:.2f}, {y:.2f}, {z:.2f}] > ").strip().split()
            if not cmd:
                continue
            c = cmd[0].lower()
            cur = pr.where()                    # jog from where the head REALLY is
            if cur is not None:
                x, y, z = cur
            try:
                if c in ("x", "y", "z") and len(cmd) == 2:
                    d = float(cmd[1])
                    if c == "x": x += d
                    elif c == "y": y += d
                    else:
                        if z + d < float(cal["z_plate_top"]) - 0.3:
                            print("refused: that would push the nozzle into the plate")
                            continue
                        z += d
                    pr.move_to(x, y, z)
                elif c == "m" and len(cmd) == 2:
                    x, y = marker_xy(int(cmd[1]))
                    pr.move_to(pr.pos[0], pr.pos[1], safe)
                    pr.move_to(x, y, safe)
                    z = align_z()
                    pr.move_to(x, y, z)
                elif c == "first":
                    first = (x, y)
                    print(f"marker {pair[0]} at ({x:.2f}, {y:.2f}); now `m {pair[1]}`, jog, `second`")
                elif c == "second":
                    if first is None:
                        print(f"do `first` over marker {pair[0]} before this")
                        continue
                    ox, oy, ang, err = geometry.plate_cal_from_pair(cfg, pair[0], first, pair[1], (x, y))
                    cal.update(origin_x=ox, origin_y=oy, angle_deg=ang)
                    print(f"marker 0 at ({ox}, {oy}), angle = {ang} deg (length error {err:+.2f} mm; "
                          "more than 1 mm means a marker or the alignment is off)")
                elif c == "ztop":
                    cal["z_plate_top"] = round(z, 2)
                    print(f"z_plate_top = {z:.2f}")
                elif c == "show":
                    print(cal)
                elif c == "save":
                    for k in ("origin_x", "origin_y", "angle_deg", "z_plate_top"):
                        cfgmod.set_value(cfg["_path"], "calibration", k, float(cal[k]))
                    stamp = time.strftime("%Y-%m-%d %H:%M")
                    cfgmod.set_value(cfg["_path"], "calibration", "plate_cal_date", stamp)
                    tpl = Path(__file__).resolve().parents[1] / "configs" / "a5_tinysa.toml"
                    if tpl.exists() and tpl.resolve() != Path(cfg["_path"]).resolve():
                        for k in geometry.PLATE_CAL_KEYS:
                            cfgmod.set_value(tpl, "calibration", k, float(cal[k]))
                        cfgmod.set_value(tpl, "calibration", "plate_cal_date", stamp)
                    print(f"saved to {cfg['_path']} (plate cal also to the machine template)")
                elif c in ("q", "quit", "exit"):
                    break
                else:
                    print(HELP)
            except (ValueError, KeyError) as e:
                print(f"? {e}")
    finally:
        pr.move_to(x, y, safe)
        pr.close()
