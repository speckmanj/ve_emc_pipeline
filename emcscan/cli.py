"""Command line:  emcscan <command> ...   (or: python -m emcscan <command> ...)

Run `emcscan -h` for the list, `emcscan <command> -h` for details.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "configs" / "a1mini_tinysa.toml"


def _cfg(path):
    from . import config
    return config.load(path)


def cmd_stl(a):
    from .cad import build_all
    build_all(_cfg(a.config), Path(a.out), a.cad)


def cmd_markers(a):
    from .markers import make_sheet
    for p in make_sheet(_cfg(a.config), Path(a.out)):
        print(p)


def cmd_plan(a):
    from .plan import build_plan, check_plan, estimate_seconds
    from .scan import fmt_s
    from . import geometry
    cfg = _cfg(a.config)
    plan = build_plan(cfg)
    probs = check_plan(cfg, plan)
    s = cfg["scan"]
    print(f"area X {s['x_min']}..{s['x_max']}  Y {s['y_min']}..{s['y_max']} mm, step {s['step']} mm, "
          f"heights {s['heights']} -> {len(plan)} points")
    print(f"minimum safe height {geometry.min_height(cfg):.1f} mm above the board")
    print(f"nozzle X {min(p.nx for p in plan):.1f}..{max(p.nx for p in plan):.1f}  "
          f"Y {min(p.ny for p in plan):.1f}..{max(p.ny for p in plan):.1f}  "
          f"Z {min(p.nz for p in plan):.2f}..{max(p.nz for p in plan):.2f}")
    print(f"estimated time {fmt_s(estimate_seconds(cfg, plan))} (assumes 0.5 s per band sweep)")
    print("SAFE TO RUN" if not probs else "PROBLEMS:\n  - " + "\n  - ".join(probs))
    if a.gcode:
        pc = cfg["printer"]
        with open(a.gcode, "w") as fh:
            fh.write(f"; emcscan path for {cfg['job']['name']} (for inspection only, not for printing)\n")
            fh.write(f"G90\nG1 Z{pc['safe_z']} F{pc['feed_z']:.0f}\n")
            last_z = None
            for p in plan:
                if p.nz != last_z:
                    fh.write(f"G1 Z{pc['safe_z']} F{pc['feed_z']:.0f}\nG1 X{p.nx:.3f} Y{p.ny:.3f} "
                             f"F{pc['feed_xy']:.0f}\nG1 Z{p.nz:.3f} F{pc['feed_z']:.0f}\n")
                    last_z = p.nz
                fh.write(f"G1 X{p.nx:.3f} Y{p.ny:.3f} F{pc['feed_xy']:.0f} ; pt {p.idx}\n")
            fh.write(f"G1 Z{pc['safe_z']} F{pc['feed_z']:.0f}\n")
        print(f"path written to {a.gcode}")
    if a.show:
        import numpy as np
        import pyvista as pv
        pl = pv.Plotter(title="planned path (plate frame)")
        pts = np.array([[p.x, p.y, p.h] for p in plan])
        pl.add_mesh(pv.lines_from_points(pts), color="orange", line_width=2)
        pl.add_mesh(pv.PolyData(pts[:1]), color="green", point_size=14, render_points_as_spheres=True)
        pl.show_grid(); pl.show()
    sys.exit(1 if probs else 0)


def cmd_printer_test(a):
    from .bench import printer_test
    printer_test(_cfg(a.config))


def cmd_analyzer_test(a):
    from .bench import analyzer_test
    analyzer_test(_cfg(a.config))


def cmd_home(a):
    from .bench import home
    home(_cfg(a.config))


def cmd_calibrate(a):
    from .bench import calibrate
    calibrate(_cfg(a.config))


def cmd_goto(a):
    from .bench import goto
    goto(_cfg(a.config), a.x, a.y, a.h)


def cmd_photo(a):
    import cv2
    from . import config as cfgmod
    from .photo import pick_area, rectify, save_rectified
    cfg = _cfg(a.config)
    img = cv2.imread(a.image)
    if img is None:
        raise SystemExit(f"cannot read {a.image}")
    rect, ext, _ = rectify(cfg, img)
    out = Path(cfg["_path"]).with_name(Path(cfg["_path"]).stem + "_photo.png")
    save_rectified(rect, ext, out)
    cfgmod.set_value(cfg["_path"], "job", "photo_rectified", out.name)
    print(f"rectified photo: {out}")
    if a.pick:
        area = pick_area(rect, ext)
        if area:
            for k, v in zip(("x_min", "x_max", "y_min", "y_max"), area):
                cfgmod.set_value(cfg["_path"], "scan", k, float(v))
            print(f"scan area set to X {area[0]}..{area[1]}  Y {area[2]}..{area[3]} mm")


def cmd_scan(a):
    from .scan import run_scan
    run_scan(_cfg(a.config), resume=a.resume, live=a.live)


def cmd_live(a):
    from .live import main
    main(a.run, a.band, a.screenshot)


def cmd_report(a):
    from .report import make_report
    p = make_report(a.run)
    if a.open:
        import webbrowser
        webbrowser.open(p.as_uri())


def cmd_compare(a):
    from .compare import compare
    compare(a.before, a.after)


def _demo_photo(cfg):
    """Draw a fake 'rectified photo' of a board for the demo (so the overlay can be seen)."""
    import cv2
    import numpy as np
    from . import geometry
    from .config import resolve
    from .photo import save_rectified
    out = resolve(cfg, cfg["job"]["photo_rectified"])
    out.parent.mkdir(parents=True, exist_ok=True)
    x0, x1, y0, y1 = geometry.plate_extent(cfg)
    ppm = 8.0
    img = np.full((int((y1 - y0) * ppm), int((x1 - x0) * ppm), 3), (150, 150, 150), np.uint8)
    px = lambda x, y: (int((x - x0) * ppm), int((y1 - y) * ppm))
    cv2.rectangle(img, px(4, 74), px(76, 16), (40, 110, 30), -1)          # the PCB
    parts = [(25, 35, 8, 6, "BUCK"), (52, 58, 10, 10, "MCU"), (64, 30, 9, 5, "USB"),
             (40, 45, 4, 3, ""), (15, 60, 6, 4, ""), (35, 25, 5, 3, "")]
    for x, y, w, h, name in parts:
        cv2.rectangle(img, px(x - w / 2, y + h / 2), px(x + w / 2, y - h / 2), (25, 25, 25), -1)
        if name:
            cv2.putText(img, name, px(x - w / 2, y - h / 2 - 3), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (235, 235, 235), 1, cv2.LINE_AA)
    save_rectified(img, [x0, x1, y0, y1], out)


def cmd_demo(a):
    """Full simulated job: scan, report, then a 'fixed' board and a before/after compare."""
    from . import config as cfgmod
    from .compare import compare
    from .report import make_report
    from .scan import run_scan
    cfg = _cfg(ROOT / "configs" / "demo_sim.toml")
    _demo_photo(cfg)
    if a.live:
        cfg["sim"]["time_scale"] = 0.05
    before = run_scan(cfg, live=a.live)
    make_report(before)
    fixed = cfgmod.load(ROOT / "configs" / "demo_sim.toml")
    fixed["job"]["name"] = "demo_after_fix"
    fixed["sim"]["sources"][0]["level_dbm"] -= 15      # pretend the fix took 15 dB off source 1
    after = run_scan(fixed)
    make_report(after)
    compare(before, after)


def main(argv=None):
    p = argparse.ArgumentParser(prog="emcscan", description="Near-field EMC scanner")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        sp = sub.add_parser(name, help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("stl", cmd_stl, "build the printable STL parts into cad/out")
    sp.add_argument("--config", default=str(TEMPLATE)); sp.add_argument("--cad", default=None)
    sp.add_argument("--out", default=str(ROOT / "cad" / "out"))
    sp = add("markers", cmd_markers, "make the printable paper marker sheet (PDF + PNG)")
    sp.add_argument("--config", default=str(TEMPLATE)); sp.add_argument("--out", default=str(ROOT / "cad" / "out"))
    sp = add("plan", cmd_plan, "check a job: points, safety, time; optional path preview")
    sp.add_argument("config"); sp.add_argument("--show", action="store_true", help="3D path preview")
    sp.add_argument("--gcode", help="also write the moves to a .gcode file for inspection")
    for name, fn, h in (("printer-test", cmd_printer_test, "connect to the printer, heaters off, no motion"),
                        ("analyzer-test", cmd_analyzer_test, "connect to the TinySA and sweep each band"),
                        ("home", cmd_home, "home the printer (bed must be empty)"),
                        ("calibrate", cmd_calibrate, "interactive: find marker 0, plate angle, plate top Z")):
        add(name, fn, h).add_argument("config")
    sp = add("goto", cmd_goto, "park the probe over a plate point to probe by hand")
    sp.add_argument("config"); sp.add_argument("x", type=float); sp.add_argument("y", type=float)
    sp.add_argument("h", type=float)
    sp = add("photo", cmd_photo, "rectify a board photo using the 4 markers")
    sp.add_argument("config"); sp.add_argument("image")
    sp.add_argument("--pick", action="store_true", help="click two corners to set the scan area")
    sp = add("scan", cmd_scan, "run a scan")
    sp.add_argument("config"); sp.add_argument("--live", action="store_true", help="open the live viewer")
    sp.add_argument("--resume", metavar="RUN_DIR", help="continue a stopped run")
    sp = add("live", cmd_live, "live 3D view of a run (in progress or finished)")
    sp.add_argument("run"); sp.add_argument("--band", type=int, default=None)
    sp.add_argument("--screenshot", default=None, help="save one image and exit")
    sp = add("report", cmd_report, "heat maps, hotspots, spectra and report.html for a run")
    sp.add_argument("run"); sp.add_argument("--open", action="store_true")
    sp = add("compare", cmd_compare, "before/after comparison of two runs on the same grid")
    sp.add_argument("before"); sp.add_argument("after")
    sp = add("demo", cmd_demo, "simulated end-to-end job, no hardware needed")
    sp.add_argument("--live", action="store_true")

    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
