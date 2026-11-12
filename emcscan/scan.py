"""The scan loop: move, settle, sweep every band, save, repeat.

Order of events for `emcscan scan job.toml`:
    1. load config, build the plan, refuse to start if any safety check fails
    2. create the run folder (or reopen it with --resume)
    3. connect printer and analyzer; heaters off; go to safe_z
    4. LNA decision (analyzer.lna = auto): quick 5 x 5 pre-scan with the LNA off;
       LNA on only if the strongest signal is below lna_threshold_dbm (else it overloads)
    5. for each planned point: move, settle, sweep each band, append to the run folder,
       update status.json (the live viewer watches this)
    6. at the end, or on Ctrl+C or any error: lift to safe_z and record the final state
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from . import geometry
from .analyzers import make_analyzer
from .plan import build_plan, check_plan, estimate_seconds
from .printers import make_printer
from .runstore import RunWriter, read_points


def fmt_s(s: float) -> str:
    s = int(max(s, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def decide_lna(cfg, printer, analyzer, plan) -> tuple[bool, float | None]:
    mode = str(cfg["analyzer"]["lna"]).lower()
    if mode in ("on", "true"):
        return True, None
    if mode in ("off", "false"):
        return False, None
    # auto: 5 x 5 pre-scan at the first height, LNA off (any strong spot -> LNA stays off)
    first_h = plan[0].h
    pts = [p for p in plan if p.h == first_h]
    xs = sorted({p.x for p in pts}); ys = sorted({p.y for p in pts})
    pick = lambda v: sorted({v[round(i * (len(v) - 1) / 4)] for i in range(5)})
    analyzer.set_lna(False)
    peak = -999.0
    print("[scan] LNA auto: pre-scan 5 x 5 with LNA off")
    for y in pick(ys):
        for x in pick(xs):
            printer.move_to(*geometry.plate_to_nozzle(cfg, x, y, first_h))
            for band in cfg["analyzer"]["bands"]:
                _, lv = analyzer.sweep(band, (x, y, first_h))
                peak = max(peak, float(np.max(lv)))
    on = peak < float(cfg["analyzer"]["lna_threshold_dbm"])
    print(f"[scan] pre-scan peak {peak:.1f} dBm -> LNA {'ON' if on else 'OFF'}")
    return on, peak


def run_scan(cfg, resume: str | None = None, live: bool = False) -> Path:
    plan = build_plan(cfg)
    problems = check_plan(cfg, plan)
    if problems:
        print("Refusing to scan:")
        for p in problems:
            print("  -", p)
        sys.exit(2)

    if resume:
        rw = RunWriter.open_existing(Path(resume))
        done = {int(r["idx"]) for r in read_points(rw.dir)}
        print(f"[scan] resuming {rw.dir.name}: {len(done)}/{len(plan)} points already done")
    else:
        rw = RunWriter.create(cfg, plan)
        done = set()
        print(f"[scan] run folder: {rw.dir}")
    todo = [p for p in plan if p.idx not in done]
    est = estimate_seconds(cfg, todo)
    print(f"[scan] {len(todo)} points, {len(cfg['analyzer']['bands'])} band(s), "
          f"estimated {fmt_s(est)} (refined after the first points)")

    viewer = None
    if live:
        viewer = subprocess.Popen([sys.executable, "-m", "emcscan", "live", str(rw.dir)])

    printer = make_printer(cfg)
    analyzer = make_analyzer(cfg)
    state = "error"
    try:
        printer.connect()
        analyzer.connect()
        printer.prepare()
        rw.status(state="starting", done=len(done), total=len(plan))
        safe = float(cfg["printer"]["safe_z"])
        first = todo[0] if todo else plan[0]
        printer.move_to(first.nx, first.ny, safe)

        if resume:
            import json
            meta = json.loads((rw.dir / "meta.json").read_text())
            lna_on = bool(meta["lna_on"])
            analyzer.set_lna(lna_on)
        else:
            lna_on, pre_peak = decide_lna(cfg, printer, analyzer, plan)
            analyzer.set_lna(lna_on)
            rw.write_meta({
                "job": cfg["job"]["name"], "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                "analyzer": analyzer.info(), "printer": cfg["printer"]["type"],
                "probe": cfg["probe"], "lna_on": lna_on, "prescan_peak_dbm": pre_peak,
                "bands": cfg["analyzer"]["bands"], "n_points": len(plan),
                "step": cfg["scan"]["step"], "heights": cfg["scan"]["heights"],
            })

        overload_lvl = float(cfg["analyzer"]["overload_dbm"])
        t0 = time.time()
        n_done_here = 0
        for p in todo:
            printer.move_to(p.nx, p.ny, p.nz)
            traces = []
            for k, band in enumerate(cfg["analyzer"]["bands"]):
                f, lv = analyzer.sweep(band, (p.x, p.y, p.h))
                rw.save_freqs(k, f)
                traces.append((f, lv))
            overload = lna_on and max(float(np.max(lv)) for _, lv in traces) > overload_lvl
            rw.append(p, traces, lna_on, overload)
            done.add(p.idx)
            n_done_here += 1
            per_pt = (time.time() - t0) / n_done_here
            eta = per_pt * (len(plan) - len(done))
            best = max(range(len(traces)), key=lambda k: float(np.max(traces[k][1])))
            f, lv = traces[best]
            i = int(np.argmax(lv))
            rw.status(state="running", done=len(done), total=len(plan), eta_s=eta,
                      x=p.x, y=p.y, h=p.h, peak_dbm=float(lv[i]), peak_hz=float(f[i]),
                      lna=lna_on)
            print(f"\r[{len(done):5d}/{len(plan)}] x={p.x:6.1f} y={p.y:6.1f} h={p.h:4.1f}  "
                  f"peak {lv[i]:6.1f} dBm @ {f[i] / 1e6:8.2f} MHz"
                  f"{'  OVERLOAD?' if overload else ''}  ETA {fmt_s(eta)}  ", end="", flush=True)
        print()
        state = "finished"
    except KeyboardInterrupt:
        print("\n[scan] stopped by user. Resume with:  emcscan scan <config> --resume", rw.dir)
        state = "stopped"
    finally:
        try:
            printer.lift()
        except Exception as e:                       # noqa: BLE001
            print(f"[scan] could not lift: {e}")
        for dev in (analyzer, printer):
            try:
                dev.close()
            except Exception:                        # noqa: BLE001
                pass
        rw.status(state=state, done=len(done), total=len(plan), eta_s=0)
        try:
            import json
            meta = json.loads((rw.dir / "meta.json").read_text())
            meta["ended"] = time.strftime("%Y-%m-%d %H:%M:%S")
            meta["state"] = state
            rw.write_meta(meta)
        except OSError:
            pass
    print(f"[scan] {state}: {len(done)}/{len(plan)} points in {rw.dir}")
    return rw.dir
