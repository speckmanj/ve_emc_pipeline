"""Self-test, no hardware needed:   .venv\\Scripts\\python tests\\run_tests.py

Covers: config write-back, plan safety checks, STL parts, photo registration through a
tilted fake photo, the TinySA text parser (fake serial port), a simulated scan that is
interrupted and resumed, the report, and the before/after comparison.
"""
from __future__ import annotations

import json
import math
import shutil
import sys
import traceback
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "tests" / "out"
OUT.mkdir(parents=True, exist_ok=True)

from emcscan import config, geometry, plan as planmod          # noqa: E402

RESULTS = []


def test(fn):
    try:
        fn()
        RESULTS.append((fn.__name__, "PASS", ""))
    except Exception as e:                                     # noqa: BLE001
        RESULTS.append((fn.__name__, "FAIL", f"{e}\n{traceback.format_exc()}"))
    return fn


def sim_cfg(name="t", **scan):
    src = OUT / f"{name}.toml"
    shutil.copy(ROOT / "configs" / "demo_sim.toml", src)
    cfg = config.load(src)
    cfg["job"]["name"] = name
    cfg["job"]["photo_rectified"] = ""
    cfg["output"]["runs_dir"] = str(OUT / "runs")
    cfg["scan"].update(scan)
    return cfg


@test
def config_write_back():
    p = OUT / "wb.toml"
    shutil.copy(ROOT / "configs" / "a5_tinysa.toml", p)
    config.set_value(p, "calibration", "origin_x", 77.25)
    config.set_value(p, "calibration", "z_plate_top", 12.4)
    config.set_value(p, "job", "photo_rectified", "x_photo.png")
    c = config.load(p)
    assert c["calibration"]["origin_x"] == 77.25
    assert c["calibration"]["z_plate_top"] == 12.4
    assert c["job"]["photo_rectified"] == "x_photo.png"
    assert "(Setup tab > Machine)" in p.read_text()            # comments survive
    assert abs(geometry.board_top_z(c) - (12.4 + 1.6)) < 1e-9


@test
def plan_safety():
    c = sim_cfg()
    assert planmod.check_plan(c, planmod.build_plan(c)) == []
    c["scan"]["heights"] = [3.0]                                # below 3 + 1 + 3 = 7
    assert any("too low" in e for e in planmod.check_plan(c, planmod.build_plan(c)))
    c = sim_cfg(); c["calibration"]["origin_x"] = 290.0         # runs off the 300 mm A5 bed
    assert any("outside" in e for e in planmod.check_plan(c, planmod.build_plan(c)))
    c = sim_cfg(); c["scan"]["x_max"] = 500.0
    assert any("beyond the plate" in e for e in planmod.check_plan(c, planmod.build_plan(c)))


@test
def geometry_round_trip():
    c = sim_cfg()
    c["calibration"].update(origin_x=50.0, origin_y=40.0, angle_deg=2.5)
    nx, ny, _ = geometry.plate_to_nozzle(c, 30.0, 20.0, 7.0)
    x, y = geometry.nozzle_to_plate_xy(c, nx, ny)
    assert abs(x - 30) < 1e-9 and abs(y - 20) < 1e-9


@test
def plate_cal_from_back_pair():
    # A5 cover: tip only reaches the back markers; marker 0 must come out right anyway.
    c = config.load(ROOT / "configs" / "a5_tinysa.toml")
    truth = dict(origin_x=12.3, origin_y=-4.5, angle_deg=1.7)
    c["calibration"].update(truth)
    i, j = geometry.cal_pair(c)
    assert (i, j) == (3, 2)
    m = geometry.marker_centers_plate(c)
    p_i = geometry.plate_to_nozzle(c, *m[i], 0)[:2]
    p_j = geometry.plate_to_nozzle(c, *m[j], 0)[:2]
    ox, oy, ang, err = geometry.plate_cal_from_pair(c, i, p_i, j, p_j)
    assert abs(ox - 12.3) < 0.01 and abs(oy + 4.5) < 0.01 and abs(ang - 1.7) < 0.001 and abs(err) < 1e-6
    # a 1.5 mm jog miss on marker 2 along the edge shows up as length error
    _, _, _, err = geometry.plate_cal_from_pair(c, i, p_i, j, (p_j[0] + 1.5, p_j[1]))
    assert 1.4 < err < 1.6


@test
def cad_parts():
    from emcscan.cad import build_all
    build_all(config.load(ROOT / "configs" / "a1mini_tinysa.toml"), OUT / "cad")   # --a1mini set
    for n in ("spacer_plate", "probe_mount_x", "probe_mount_y", "probe_gauge"):
        assert (OUT / "cad" / f"{n}.stl").stat().st_size > 1000


@test
def photo_registration():
    """Render the plate top view, tilt it like a phone photo, rectify, measure the error."""
    from emcscan.markers import render_plate_image
    from emcscan.photo import detect_markers, rectify
    c = config.load(ROOT / "configs" / "a5_tinysa.toml")
    top = cv2.cvtColor(render_plate_image(c, 6.0), cv2.COLOR_GRAY2BGR)
    h, w = top.shape[:2]
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([[180, 90], [w + 120, 40], [w + 260, h + 210], [60, h + 120]])
    canvas = cv2.warpPerspective(top, cv2.getPerspectiveTransform(src, dst), (w + 400, h + 350),
                                 borderValue=(60, 60, 60))
    canvas = cv2.GaussianBlur(canvas, (3, 3), 0)
    cv2.imwrite(str(OUT / "fake_photo.jpg"), canvas)
    rect, ext, _ = rectify(c, cv2.imread(str(OUT / "fake_photo.jpg")), px_per_mm=8.0)
    found = detect_markers(cv2.cvtColor(rect, cv2.COLOR_RGB2BGR))
    x0, x1, y0, y1 = ext
    worst = 0.0
    for i, (px, py) in geometry.marker_centers_plate(c).items():
        u, v = found[i]
        err = np.hypot(u / 8.0 + x0 - px, y1 - v / 8.0 - py)
        worst = max(worst, err)
    assert worst < 0.5, f"marker position error {worst:.2f} mm"
    print(f"    photo registration worst error {worst:.3f} mm")


@test
def tinysa_parser():
    from emcscan.analyzers import TinySA

    class FakeSerial:
        """Mimics fw v1.4-200: two-line version, 3-column scan lines, optional short replies."""
        def __init__(self, short_replies=0, overflow=False):
            self.buf = b""
            self.short = short_replies
            self.overflow = overflow
            self.log = []

        def write(self, data):
            cmd = data.decode().strip()
            self.log.append(cmd)
            out = (cmd + "\r\n") if cmd else ""
            if cmd == "version":
                out += "tinySA4_v1.4-fake\r\nHW Version:V0.5.4 max2871\r\n"
            elif cmd.startswith("scan"):
                _, a, b, n, _m = cmd.split()
                pts = np.linspace(float(a), float(b), int(n))
                if self.short:
                    self.short -= 1
                    pts = pts[:-4]                      # the 446-of-450 case seen on the bench
                for i, f in enumerate(pts):
                    if self.overflow and i % 50 == 7:   # fw prints -100 dBm like this
                        out += f"{f:.0f} -:.000000e+01 0.000000000 \r\n"
                    else:
                        out += f"{f:.0f} {-80.0 + f / 1e9:.6e} 0.000000000 \r\n"
            self.buf += out.encode() + b"ch> "

        def read(self, n):
            d, self.buf = self.buf[:n], self.buf[n:]
            return d

        def reset_input_buffer(self):
            pass

        def close(self):
            pass

    def fresh(short=0, overflow=False):
        t = TinySA(config.load(ROOT / "configs" / "a5_tinysa.toml"))
        t.ser = FakeSerial(short, overflow)
        t.ser.write(b"\r"); t._read_until_prompt(1)
        return t

    band = {"name": "30-300 MHz", "start_hz": 30e6, "stop_hz": 300e6, "points": 450}
    t = fresh()
    lines = [ln.strip() for ln in t.cmd("version").split("\n") if ln.strip()]
    assert lines == ["tinySA4_v1.4-fake", "HW Version:V0.5.4 max2871"]
    f, lv = t.sweep(band)
    assert len(f) == 450 and abs(f[-1] - 300e6) < 1 and abs(lv[0] - (-80 + 0.03)) < 1e-3

    p = TinySA.parse_number                              # fw digit overflow ":" = 10
    assert p("-:.000000e+01") == -100.0 and p("-9.150000e+01") == -91.5
    assert abs(p("9.:00000e+01") - 100.0) < 1e-9 and p("40222717") == 40222717.0
    for junk in ("-:x", "abc", "-:.0e+0x"):
        try:
            p(junk)
            raise AssertionError(f"parsed junk {junk!r}")
        except ValueError:
            pass

    t = fresh(overflow=True)                             # -100 dBm lines kept, no retry
    f, lv = t.sweep(band)
    assert len(f) == 450 and t.retries == 0 and lv[7] == -100.0 and lv[57] == -100.0

    t = fresh(short=1)                                   # one short reply: retried, succeeds
    f, lv = t.sweep(band)
    assert len(f) == 450 and t.retries == 1

    t = fresh(short=2)                                   # two in a row: clear error
    try:
        t.sweep(band)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "after a retry" in str(e)

    t = fresh()                                          # close() hands back power-on state
    t.close()
    assert t.ser.log[-3:] == ["lna off", "rbw auto", "resume"]

    cfg = config.load(ROOT / "configs" / "a5_tinysa.toml")
    for b in cfg["analyzer"]["bands"]:                   # no shipped band straddles 800 MHz
        assert not (b["start_hz"] < TinySA.ULTRA_START_HZ < b["stop_hz"]), b["name"]


@test
def scan_analyzer_auto_recovery():
    # 2026-11-23: the TinySA stalled every ~160 points; the scan must ride through it.
    from emcscan import analyzers, scan as scanmod
    from emcscan.runstore import Run
    c = sim_cfg("recover", x_min=10.0, x_max=25.0, y_min=20.0, y_max=35.0, step=3.0, heights=[7.0])
    n_total = len(planmod.build_plan(c))
    orig_sweep, orig_rc, orig_waits = (analyzers.SimAnalyzer.sweep, analyzers.Analyzer.reconnect,
                                       scanmod.RECONNECT_WAITS_S)
    count = {"n": 0, "rc": 0}

    def stalls(self, band, probe_xyz=None):
        count["n"] += 1
        if count["n"] > 60 and count["n"] % 40 == 0:        # after the pre-scan, every 40 sweeps
            raise TimeoutError("TinySA did not answer in 60 s")
        return orig_sweep(self, band, probe_xyz)

    def rc(self):
        count["rc"] += 1
        return orig_rc(self)

    analyzers.SimAnalyzer.sweep, analyzers.Analyzer.reconnect = stalls, rc
    scanmod.RECONNECT_WAITS_S = (0.0,)
    try:
        run_dir = scanmod.run_scan(c, log=lambda *_: None)
        meta = json.loads((run_dir / "meta.json").read_text())
        assert meta["state"] == "finished" and len(Run(run_dir).done_idx) == n_total
        assert len(meta["recoveries"]) == count["rc"] >= 2 and all(e["ok"] for e in meta["recoveries"])

        def dead(self, band, probe_xyz=None):               # never answers: must give up, not loop
            raise TimeoutError("TinySA did not answer in 60 s")
        analyzers.SimAnalyzer.sweep = dead
        count["rc"] = 0
        c["analyzer"]["lna_threshold_dbm"] = -999.0
        c["job"]["name"] = "recover_dead"
        try:
            scanmod.run_scan(c, resume=None, log=lambda *_: None)
            raise AssertionError("expected a give-up")
        except RuntimeError as e:
            assert "recovery limit" in str(e), e
        assert count["rc"] == 3, count                     # point_retries = 3, then stop
    finally:
        analyzers.SimAnalyzer.sweep, analyzers.Analyzer.reconnect = orig_sweep, orig_rc
        scanmod.RECONNECT_WAITS_S = orig_waits


@test
def scan_interrupt_resume_report_compare():
    from emcscan import analyzers
    from emcscan.compare import compare
    from emcscan.report import make_report
    from emcscan.runstore import Run
    from emcscan.scan import run_scan
    c = sim_cfg("resume", x_min=10.0, x_max=40.0, y_min=20.0, y_max=50.0, step=3.0, heights=[7.0])
    n_total = len(planmod.build_plan(c))
    orig = analyzers.SimAnalyzer.sweep
    count = {"n": 0}

    def flaky(self, band, probe_xyz=None):
        count["n"] += 1
        if count["n"] == 25 * 2 + 50:          # pre-scan (25 pts x 2 bands) + 25 points
            raise KeyboardInterrupt
        return orig(self, band, probe_xyz)

    analyzers.SimAnalyzer.sweep = flaky
    try:
        run_dir = run_scan(c)
    finally:
        analyzers.SimAnalyzer.sweep = orig
    partial = len(Run(run_dir).done_idx)
    assert 0 < partial < n_total, partial
    run_scan(c, resume=str(run_dir))
    r = Run(run_dir)
    assert len(r.done_idx) == n_total and len(set(r.done_idx)) == n_total
    assert r.traces[0].shape == (n_total, 450)
    make_report(run_dir)
    assert (run_dir / "report" / "report.html").stat().st_size > 50_000
    c2 = sim_cfg("resume_after", x_min=10.0, x_max=40.0, y_min=20.0, y_max=50.0, step=3.0,
                 heights=[7.0])
    c2["sim"]["sources"][0]["level_dbm"] -= 15
    run2 = run_scan(c2)
    html = compare(run_dir, run2)
    assert html.exists()
    hot = (run_dir / "report" / "hotspots.csv").read_text()
    assert "25" in hot or "24" in hot or "22" in hot or "28" in hot, hot


def _toy_board():
    """PCB 50 x 30 x 1.6 with one 10 x 10 x 8 part centered at (25, 15), board frame."""
    import trimesh
    from emcscan import heightmap
    pcb = trimesh.creation.box((50, 30, 1.6)); pcb.apply_translation((25, 15, -0.8))
    part = trimesh.creation.box((10, 10, 8)); part.apply_translation((25, 15, 4))
    m = trimesh.util.concatenate([pcb, part])
    return heightmap.StepBoard(np.asarray(m.vertices), np.asarray(m.faces), 1.6, (50.0, 30.0), 8.0,
                               2, "toy", False)


@test
def heightmap_ring_and_plan():
    from emcscan import heightmap
    board = _toy_board()
    c = sim_cfg("hm", x_min=5.0, x_max=45.0, y_min=5.0, y_max=25.0, step=1.0, heights=[2.0],
                clearance=1.0)
    c["printer"]["safe_z"] = 80.0
    c["probe"].update(name="H20", orientation="x", offset_z=0.0)
    c["printer"]["has_nozzle"] = False
    s = heightmap.empty_setup()
    s["margin_xy"] = 0.0
    s["step"] = {"file": "toy", "reg": {"b": [[0, 0], [50, 0]], "p": [[10, 20], [60, 20]]}}
    hx = heightmap.compute(c, s, board=board)
    # part top 8 mm at plate (35, 35); the H20 ring (R 11.9) along X: right over the part
    # the tip needs 8 + 1; 6 mm to the side in X the ring bottom is 1.68 mm up -> 7.32 + 1
    assert abs(float(hx.req_at(35.0, 35.0)) - 9.0) < 0.3, hx.req_at(35.0, 35.0)
    r = 11.9; u = 6.0 + 5.0                                     # 6 mm past the part edge (half 5)
    want = 8 - (r - math.sqrt(r * r - 6.0 ** 2)) + 1.0
    got = float(hx.req[int((35 - hx.y0) / hx.res), int((35 + u - hx.x0) / hx.res)])
    assert abs(got - want) < 0.6, (got, want)
    # blade along X: 4 mm to the side in Y (thin direction) is already clear
    assert float(hx.req_at(35.0, 35.0 + 5.0 + 3.0)) < 2.0, hx.req_at(35.0, 43.0)
    c["probe"]["orientation"] = "y"
    hy = heightmap.compute(c, s, board=board)
    assert float(hy.req_at(35.0, 43.0)) > 7.0                   # now the ring spans Y
    # plan: raised near the part, low far away, every travel line legal
    c["scan"].update(x_min=12.0, x_max=58.0, y_min=22.0, y_max=48.0, step=2.0)
    c["probe"]["orientation"] = "x"
    hx = heightmap.compute(c, s, board=board)
    plan = planmod.build_plan(c, hx)
    assert planmod.check_plan(c, plan) == [], planmod.check_plan(c, plan)
    near = min(plan, key=lambda p: math.hypot(p.x - 35, p.y - 35))
    far = min(plan, key=lambda p: math.hypot(p.x - 12, p.y - 48))
    assert near.raised and near.ht >= 9.0 and not far.raised and far.ht == 2.0
    zb = geometry.board_top_z(c)
    for a_, b_ in zip(plan, plan[1:]):
        assert b_.vz - zb >= hx.via(a_.x, a_.y, b_.x, b_.y) - 1e-6
    # a point forced below the map is refused
    near.ht -= 3; near.nz -= 3
    assert any("below the legal height" in e for e in planmod.check_plan(c, plan))


@test
def heightmap_marks_boxes_flat_and_registration():
    from emcscan import heightmap
    c = sim_cfg("hm2", x_min=0.0, x_max=40.0, y_min=0.0, y_max=40.0, step=2.0, heights=[2.0])
    c["probe"].update(name="H5", orientation="x")
    s = heightmap.empty_setup()
    s["marks"] = [{"x": 10, "y": 10, "h": 6, "r": 2}]
    s["boxes"] = [{"x0": 25, "x1": 30, "y0": 25, "y1": 35, "h": 4}]
    h = heightmap.compute(c, s)
    assert float(h.H[int((10 - h.y0) / h.res), int((10 - h.x0) / h.res)]) == 6
    assert float(h.req_at(27.5, 30)) >= 5.0 and float(h.req_at(20, 20)) < 1.5
    s["flat_mm"] = 3.0
    assert float(heightmap.compute(c, s).req_at(20, 20)) >= 4.0
    th, t, err = heightmap.registration({"b": [[0, 0], [10, 0]], "p": [[5, 5], [5, 15]]})
    assert abs(math.degrees(th) - 90) < 1e-6 and abs(err) < 1e-9
    P = heightmap.board_to_plate(np.array([[10.0, 0, 0]]), th, t)
    assert np.allclose(P[0, :2], [5, 15])


if __name__ == "__main__":
    print()
    w = max(len(n) for n, _, _ in RESULTS)
    for name, res, msg in RESULTS:
        print(f"{name:<{w}}  {res}")
        if msg:
            print("    " + msg.replace("\n", "\n    "))
    bad = sum(r != "PASS" for _, r, _ in RESULTS)
    print(f"\n{len(RESULTS) - bad}/{len(RESULTS)} passed")
    sys.exit(1 if bad else 0)
