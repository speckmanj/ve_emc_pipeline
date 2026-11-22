"""emcscan app: one window for a whole scan job.

    emcscan app [job.toml]          (or double-click "EMC Scanner.bat" in the repo folder)

Tabs, left to right, in the order you work:
    1 Prep     open or create the job, probe and machine settings, board photo, STEP model,
               device tests, and a checklist of what is still missing
    2 Setup    the board seen from above: photo, STEP heights, the probe path colored by
               tip height. Place the STEP on the plate, set the scan area, teach tall parts
               by jogging the probe (or draw boxes, or set one master height for a
               low-profile board), calibrate the plate and board top, 3D preview
    3 Run      start, stop and resume the scan; live progress and a live peak map
    4 Results  runs of this job: report, heat maps, before/after comparison, 3D viewer

Runs entirely on this PC: no AI, no internet, no cloud. Customer files (STEP, photos) are
read locally and stay in the job folder. The only network traffic is to the printer when
it is a network printer (Bambu, Klipper); the A5 and the TinySA are on USB.
Everything the app does is also possible from the command line (emcscan -h), and every
setting lives in plain files in the job folder (scan.toml, scan_setup.json).
"""
from __future__ import annotations

import copy
import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Rectangle

from . import config as cfgmod
from . import geometry, heightmap
from . import theme
from .plan import build_plan, check_plan, estimate_seconds

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "configs" / "a5_tinysa.toml"
JOBS_ROOT = Path(r"C:\work\ve_emc_jobs")
PREFS = Path.home() / ".emcscan_app.json"
OK, BAD, INFO = "OK", "FIX", "--"


def fmt_s(s: float) -> str:
    s = int(max(s, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


class _Tee:
    """Sends print() output to the app's log (and the console, if there is one)."""

    def __init__(self, q, orig):
        self.q, self.orig = q, orig

    def write(self, s):
        if s:
            self.q.put(s)
        if self.orig is not None:
            try:
                self.orig.write(s)
            except Exception:                                   # noqa: BLE001
                pass

    def flush(self):
        if self.orig is not None:
            try:
                self.orig.flush()
            except Exception:                                   # noqa: BLE001
                pass


class Machine:
    """The printer and analyzer connections the app holds open (jogging, tests, scans)."""

    def __init__(self):
        self.printer = None
        self.analyzer = None
        self.homed = False
        self.lock = threading.Lock()

    @property
    def pos(self):
        return self.printer.pos if self.printer is not None else None


class App(tk.Tk):
    def __init__(self, job: str | None = None):
        super().__init__()
        self.title("Valence EMC Scanner")
        self.geometry("1500x940")
        self.minsize(1200, 760)
        self._ui_q: queue.Queue = queue.Queue()
        self._log_q: queue.Queue = queue.Queue()
        sys.stdout = _Tee(self._log_q, sys.__stdout__)
        sys.stderr = _Tee(self._log_q, sys.__stderr__)
        self.machine = Machine()
        self.cfg = None
        self.cfg_path: Path | None = None
        self.setup: dict = heightmap.empty_setup()
        self.board = None
        self._board_key = None
        self.heights = None
        self.plan = None
        self.problems: list[str] = []
        self.photo = None
        self._gen = 0
        self._done_gen = 0          # last recompute that finished; Start needs it == _gen
        self.est = 0.0
        self.scan_thread = None
        self.scan_stop = threading.Event()
        self.live_pts: list = []
        self.current_run: Path | None = None
        self._fields: list = []
        self._drag = None
        self._view_lim = None

        theme.apply(self)                       # dark: widgets, plots, title bars

        self._build_topbar()
        pw = ttk.PanedWindow(self, orient="vertical")
        pw.pack(fill="both", expand=True)
        self.nb = ttk.Notebook(pw)
        pw.add(self.nb, weight=5)
        logf = ttk.Frame(pw)
        pw.add(logf, weight=1)
        self.log = tk.Text(logf, height=7, font=("Consolas", 9), wrap="none", bg=theme.FIELD, fg=theme.TEXT,
                           insertbackground=theme.TEXT, relief="flat")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)

        self.tab_prep = ttk.Frame(self.nb); self.nb.add(self.tab_prep, text="  1  Prep  ")
        self.tab_setup = ttk.Frame(self.nb); self.nb.add(self.tab_setup, text="  2  Setup  ")
        self.tab_run = ttk.Frame(self.nb); self.nb.add(self.tab_run, text="  3  Run  ")
        self.tab_res = ttk.Frame(self.nb); self.nb.add(self.tab_res, text="  4  Results  ")
        self._build_prep()
        self._build_setup()
        self._build_run()
        self._build_results()

        self.bind_all("<Key>", self._on_key, add="+")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        job = job or self._prefs().get("last_job")
        if job and Path(job).exists():
            self.open_job(Path(job))
        else:
            self.say("No job open. Use New job or Open job (top left).")

    # ------------------------------------------------------------------ plumbing
    def say(self, msg: str):
        self._log_q.put(msg.rstrip("\n") + "\n")

    def ui(self, fn, *a):
        """Run fn on the UI thread (safe to call from any thread)."""
        self._ui_q.put((fn, a))

    def bg(self, fn, done=None, what: str = ""):
        """Run fn() in a background thread; done(result) runs on the UI thread."""
        def work():
            try:
                r = fn()
            except Exception as e:                              # noqa: BLE001
                self.say(f"[error] {what}: {e}")
                traceback.print_exc()
                self.ui(messagebox.showerror, "EMC Scanner", f"{what} failed:\n{e}")
                return
            if done is not None:
                self.ui(done, r)
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        if getattr(self, "_closing", False):
            return
        try:
            while True:
                fn, a = self._ui_q.get_nowait()
                try:
                    fn(*a)
                except Exception as e:                          # noqa: BLE001
                    self.say(f"[error] {e}")
                    traceback.print_exc()
        except queue.Empty:
            pass
        chunks = []
        try:
            while True:
                chunks.append(self._log_q.get_nowait())
        except queue.Empty:
            pass
        if chunks:
            text = "".join(chunks).replace("\r", "\n")
            self.log.insert("end", text)
            if int(self.log.index("end-1c").split(".")[0]) > 4000:
                self.log.delete("1.0", "1000.0")
            self.log.see("end")
        self.after(100, self._poll)

    def _prefs(self) -> dict:
        try:
            return json.loads(PREFS.read_text())
        except (OSError, ValueError):
            return {}

    def _save_prefs(self, **kw):
        p = self._prefs(); p.update(kw)
        try:
            PREFS.write_text(json.dumps(p))
        except OSError:
            pass

    def run_cli(self, args: list[str], what: str, done=None):
        """Run `python -m emcscan ...` in a subprocess (keeps 3D windows and heavy work out of
        the app's process); output goes to the log."""
        def work():
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            p = subprocess.Popen([sys.executable, "-m", "emcscan", *args], cwd=str(ROOT),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 creationflags=flags)
            for line in p.stdout:
                self._log_q.put(line)
            rc = p.wait()
            if rc:
                self.say(f"[{what}] exited with code {rc}")
            if done is not None:
                self.ui(done, rc)
        self.say(f"[{what}] emcscan {' '.join(args)}")
        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ job
    def _build_topbar(self):
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(fill="x")
        ttk.Button(bar, text="New job...", command=self.new_job).pack(side="left")
        ttk.Button(bar, text="Open job...", command=self.pick_job).pack(side="left", padx=4)
        ttk.Button(bar, text="Reload", command=lambda: self.open_job(self.cfg_path) if self.cfg_path else None).pack(side="left")
        ttk.Button(bar, text="Job folder", command=lambda: self.cfg and os.startfile(self.cfg["_dir"])).pack(side="left", padx=4)
        self.lbl_job = ttk.Label(bar, text="(no job)", style="Head.TLabel")
        self.lbl_job.pack(side="left", padx=12)
        self.lbl_machine = ttk.Label(bar, text="printer: not connected   analyzer: not connected")
        self.lbl_machine.pack(side="right")

    def pick_job(self):
        init = str(JOBS_ROOT) if JOBS_ROOT.exists() else str(ROOT)
        f = filedialog.askopenfilename(title="Open a scan job", initialdir=init,
                                       filetypes=[("Scan job", "*.toml"), ("All", "*.*")])
        if f:
            self.open_job(Path(f))

    def new_job(self):
        d = tk.Toplevel(self); d.title("New scan job"); d.transient(self); d.grab_set()
        v_root = tk.StringVar(value=str(JOBS_ROOT))
        v_cust, v_dev = tk.StringVar(), tk.StringVar()
        v_tpl = tk.StringVar(value=str(TEMPLATE))
        rows = [("Jobs folder", v_root), ("Customer", v_cust), ("Device / board", v_dev),
                ("Template", v_tpl)]
        for i, (lab, var) in enumerate(rows):
            ttk.Label(d, text=lab).grid(row=i, column=0, sticky="w", padx=8, pady=4)
            ttk.Entry(d, textvariable=var, width=60).grid(row=i, column=1, padx=8, pady=4)
        ttk.Label(d, text="Creates <jobs folder>\\YYYY-MM_Customer_Device\\04_scan\\scan.toml "
                          "(or opens it if it exists).", foreground=theme.DIM).grid(
            row=len(rows), column=0, columnspan=2, padx=8, sticky="w")

        def create():
            clean = lambda s: re.sub(r"[^A-Za-z0-9_-]+", "", s.replace(" ", ""))
            cust, dev = clean(v_cust.get()), clean(v_dev.get())
            if not cust or not dev:
                messagebox.showwarning("New job", "Customer and device are both needed.", parent=d)
                return
            root = Path(v_root.get())
            existing = sorted(root.glob(f"*_{cust}_{dev}")) if root.exists() else []
            job_dir = existing[-1] if existing else root / f"{time.strftime('%Y-%m')}_{cust}_{dev}"
            f = job_dir / "04_scan" / "scan.toml"
            if not f.exists():
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_text(Path(v_tpl.get()).read_text(encoding="utf-8"), encoding="utf-8")
                cfgmod.set_value(f, "job", "name", f"{cust}_{dev}")
                self.say(f"[job] created {f}")
            d.destroy()
            self.open_job(f)

        ttk.Button(d, text="Create", command=create, style="Big.TButton").grid(
            row=len(rows) + 1, column=1, sticky="e", padx=8, pady=8)

    def open_job(self, path: Path):
        try:
            cfg = cfgmod.load(path)
        except Exception as e:                                  # noqa: BLE001
            messagebox.showerror("Open job", f"Cannot read {path}:\n{e}")
            return
        self.cfg, self.cfg_path = cfg, Path(path)
        self.setup = heightmap.load_setup(cfg) or heightmap.empty_setup()
        self.photo = None
        ph = cfg["job"].get("photo_rectified")
        if ph:
            p = cfgmod.resolve(cfg, ph)
            if p.exists() and p.with_suffix(".json").exists():
                import matplotlib.image as mpimg
                self.photo = (mpimg.imread(p), json.loads(p.with_suffix(".json").read_text())["extent"])
        self.lbl_job.configure(text=f"{cfg['job']['name']}    ({path})")
        self.title(f"Valence EMC Scanner - {cfg['job']['name']}")
        self._save_prefs(last_job=str(path))
        self._load_fields()
        self._view_lim = None
        self.refresh_runs()
        self.recompute()

    def reload_cfg(self):
        self.cfg = cfgmod.load(self.cfg_path)

    def set_cfg(self, section: str, key: str, value, recompute=True):
        if self.cfg_path is None:
            return
        cfgmod.set_value(self.cfg_path, section, key, value)
        self.reload_cfg()
        if recompute:
            self.recompute()

    def save_setup(self, recompute=True):
        if self.cfg is None:
            return
        heightmap.save_setup(self.cfg, self.setup)
        if recompute:
            self.recompute()

    # ------------------------------------------------------------------ compute
    def recompute(self):
        """Height map + plan + checks, in the background (STEP rasterizing takes seconds)."""
        if self.cfg is None:
            return
        self._gen += 1
        gen = self._gen
        cfg = copy.deepcopy(self.cfg)
        setup = copy.deepcopy(self.setup)
        st = setup.get("step") or {}
        key = (st.get("file"), bool(st.get("flip"))) if st.get("file") else None
        board = self.board if key == self._board_key else None
        self.lbl_stats.configure(text="computing...")

        def work():
            b = board
            if key and b is None:
                b = heightmap.load_step(cfgmod.resolve(cfg, st["file"]), bool(st.get("flip")),
                                        cache_dir=Path(cfg["_dir"]))
            hts = None
            if heightmap.uses_heights(setup) and not (st.get("file") and not (st.get("reg") or {}).get("p")):
                hts = heightmap.compute(cfg, setup, board=b)
            elif heightmap.uses_heights(setup):
                s2 = dict(setup); s2["step"] = None
                hts = heightmap.compute(cfg, s2) if heightmap.uses_heights(s2) else None
            plan = build_plan(cfg, hts)
            probs = check_plan(cfg, plan)
            if st.get("file") and not (st.get("reg") or {}).get("p"):
                # Loaded but not placed: the STEP is left out of the height map, and the flat
                # rule (tallest_part) would quietly plan over parts it knows nothing about.
                probs.insert(0, "STEP model loaded but not placed on the plate (Setup > Place "
                                "STEP): its parts are not in the height map yet")
            est = estimate_seconds(cfg, plan) if plan else 0
            return b, key, hts, plan, probs, est

        def done(r):
            if gen != self._gen:
                return
            self._done_gen = gen
            self.board, self._board_key, self.heights, self.plan, self.problems, self.est = r
            self._after_compute()
        self.bg(work, done, "height map and plan")

    def _after_compute(self):
        plan = self.plan or []
        n_r = sum(p.raised for p in plan)
        ht = [p.ht for p in plan]
        txt = (f"{len(plan)} points ({len(self.cfg['scan']['heights'])} layer(s)), "
               f"{n_r} raised over tall parts\n"
               f"tip height {min(ht):.1f} to {max(ht):.1f} mm above board\n"
               f"estimated {fmt_s(self.est)}\n") if plan else "no points\n"
        if self.heights is not None:
            txt += "heights: " + "; ".join(self.heights.sources) + "\n"
        txt += "SAFE TO RUN" if not self.problems else "PROBLEMS:\n- " + "\n- ".join(self.problems)
        self.lbl_stats.configure(text=txt, foreground=theme.OK if not self.problems else theme.BAD)
        self.lbl_run_summary.configure(text=txt, foreground=self.lbl_stats.cget("foreground"))
        self._update_step_info()
        self.draw_setup()
        self.refresh_checks()

    # ------------------------------------------------------------------ Prep tab
    def _field(self, parent, row, label, section, key, kind="float", width=12, choices=None,
               note=""):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=4, pady=2)
        var = tk.StringVar()
        if choices:
            w = ttk.Combobox(parent, textvariable=var, values=choices, width=width, state="readonly")
            w.bind("<<ComboboxSelected>>", lambda e: self._commit(var, section, key, kind))
        else:
            w = ttk.Entry(parent, textvariable=var, width=width)
            w.bind("<Return>", lambda e: self._commit(var, section, key, kind))
            w.bind("<FocusOut>", lambda e: self._commit(var, section, key, kind))
        w.grid(row=row, column=1, sticky="w", padx=4, pady=2)
        if note:
            ttk.Label(parent, text=note, foreground=theme.DIM).grid(row=row, column=2, sticky="w")
        self._fields.append((var, section, key, kind))
        return var

    def _fmt_val(self, v, kind):
        if kind == "list":
            return ", ".join(f"{x:g}" for x in v)
        if kind == "bool":
            return "yes" if v else "no"
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    def _load_fields(self):
        for var, section, key, kind in self._fields:
            v = self.cfg.get(section, {}).get(key, "")
            var.set(self._fmt_val(v, kind))

    def _commit(self, var, section, key, kind):
        if self.cfg is None:
            return
        raw = var.get().strip()
        try:
            if kind == "float":
                val = float(raw)
            elif kind == "int":
                val = int(float(raw))
            elif kind == "list":
                val = [float(x) for x in re.split(r"[,\s]+", raw) if x]
                if not val:
                    raise ValueError("at least one value")
            elif kind == "bool":
                val = raw.lower() in ("yes", "true", "1", "on")
            else:
                val = raw
        except ValueError as e:
            messagebox.showwarning("Value", f"{section}.{key}: {e}")
            var.set(self._fmt_val(self.cfg[section].get(key, ""), kind))
            return
        if self.cfg[section].get(key) == val:
            return
        self.set_cfg(section, key, val)
        self.say(f"[job] {section}.{key} = {val}")

    def _build_prep(self):
        t = self.tab_prep
        left = ttk.Frame(t, padding=8); left.pack(side="left", fill="y")
        mid = ttk.Frame(t, padding=8); mid.pack(side="left", fill="y")
        right = ttk.Frame(t, padding=8); right.pack(side="left", fill="both", expand=True)

        f = ttk.LabelFrame(left, text="Job and board", padding=6); f.pack(fill="x", pady=4)
        self._field(f, 0, "Job name", "job", "name", "str", 24)
        self._field(f, 1, "Board thickness (mm)", "board", "thickness")
        self._field(f, 2, "Tallest part (no setup)", "board", "tallest_part", note="flat rule only")
        f = ttk.LabelFrame(left, text="Probe", padding=6); f.pack(fill="x", pady=4)
        self._field(f, 0, "Probe", "probe", "name", "str", 8, ["H20", "H10", "H5", "E5"])
        self._field(f, 1, "Blade along", "probe", "orientation", "str", 8, ["x", "y"],
                    note="x: reads Hy, y: reads Hx")
        self._field(f, 2, "Tip above nozzle (mm)", "probe", "offset_z", note="A5: 0")
        self._field(f, 3, "Mount underside (mm)", "probe", "head_h", note="above the tip")
        f = ttk.LabelFrame(left, text="Printer", padding=6); f.pack(fill="x", pady=4)
        self._field(f, 0, "Type", "printer", "type", "str", 14, ["serial_gcode", "bambu_mqtt", "moonraker", "sim"])
        self._field(f, 1, "COM port", "printer", "port", "str")
        self._field(f, 2, "Baud", "printer", "baud", "int")
        self._field(f, 3, "Host / URL", "printer", "host", "str")
        self._field(f, 4, "Safe Z (travel)", "printer", "safe_z")
        self._field(f, 5, "Z min (firmware)", "printer", "z_min")
        self._field(f, 6, "Has a nozzle", "printer", "has_nozzle", "bool", 6, ["yes", "no"])
        f = ttk.LabelFrame(left, text="Analyzer", padding=6); f.pack(fill="x", pady=4)
        self._field(f, 0, "Type", "analyzer", "type", "str", 10, ["tinysa", "sim"])
        self._field(f, 1, "COM port", "analyzer", "port", "str", note="auto = find TinySA")
        self._field(f, 2, "LNA", "analyzer", "lna", "str", 8, ["auto", "on", "off"])

        f = ttk.LabelFrame(mid, text="Board files", padding=6); f.pack(fill="x", pady=4)
        ttk.Button(f, text="Load board photo...", command=self.load_photo).grid(row=0, column=0, sticky="we", pady=2)
        self.lbl_photo = ttk.Label(f, text="no photo", width=46); self.lbl_photo.grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(f, text="Load STEP model...", command=self.load_step).grid(row=1, column=0, sticky="we", pady=2)
        self.lbl_step = ttk.Label(f, text="no STEP (optional)", width=46, wraplength=330, justify="left")
        self.lbl_step.grid(row=1, column=1, sticky="w", padx=6)
        self.v_flip = tk.BooleanVar(value=False)
        ttk.Checkbutton(f, text="Scan the BOTTOM side (board turned over)", variable=self.v_flip,
                        command=self._flip_changed).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Button(f, text="Remove STEP", command=self.remove_step).grid(row=2, column=0, sticky="we")

        f = ttk.LabelFrame(mid, text="Devices", padding=6); f.pack(fill="x", pady=4)
        ttk.Button(f, text="Connect printer", command=self.connect_printer).grid(row=0, column=0, sticky="we", pady=2)
        ttk.Button(f, text="Connect analyzer", command=self.connect_analyzer).grid(row=0, column=1, sticky="we", pady=2, padx=4)
        ttk.Button(f, text="Disconnect all", command=self.disconnect).grid(row=0, column=2, sticky="we", pady=2)
        ttk.Button(f, text="Analyzer test sweep", command=self.analyzer_test).grid(row=1, column=0, sticky="we", pady=2)
        ttk.Button(f, text="Marker sheet (PDF)", command=self.marker_sheet).grid(row=1, column=1, sticky="we", pady=2, padx=4)
        ttk.Button(f, text="Edit job file", command=lambda: self.cfg and os.startfile(self.cfg["_path"])).grid(row=1, column=2, sticky="we", pady=2)
        ttk.Label(mid, text="Bands, RBW and other rarely changed settings live in the job file "
                            "(Edit job file, then Reload).", foreground=theme.DIM, wraplength=420).pack(anchor="w", pady=6)

        ttk.Label(right, text="Checklist", style="Head.TLabel").pack(anchor="w")
        cols = ("state", "item", "detail")
        self.tv_checks = ttk.Treeview(right, columns=cols, show="headings", height=18)
        for c, w in zip(cols, (50, 170, 560)):
            self.tv_checks.heading(c, text=c.capitalize()); self.tv_checks.column(c, width=w, anchor="w")
        self.tv_checks.tag_configure("bad", foreground=theme.BAD)
        self.tv_checks.tag_configure("ok", foreground=theme.OK)
        self.tv_checks.pack(fill="both", expand=True)
        ttk.Button(right, text="Recheck", command=self.recompute).pack(anchor="e", pady=4)

    def refresh_checks(self):
        tv = self.tv_checks
        tv.delete(*tv.get_children())
        if self.cfg is None:
            return
        cfg, s = self.cfg, self.setup

        def add(state, item, detail):
            tv.insert("", "end", values=(state, item, detail),
                      tags=("ok" if state == OK else "bad" if state == BAD else "",))
        pr = cfg["probe"]
        add(OK, "Job file", cfg["_path"])
        est = " (H10 ring size is an ESTIMATE: measure it)" if str(pr["name"]).upper() == "H10" else ""
        add(OK if not est else BAD, "Probe", f"{pr['name']}, blade along {pr['orientation']}{est}")
        add(OK if self.photo is not None else INFO, "Board photo",
            "rectified" if self.photo is not None else "none: heat maps will have no board picture")
        st = s.get("step") or {}
        if st.get("file"):
            reg = st.get("reg") or {}
            np_ = len(reg.get("p") or [])
            add(OK, "STEP model", Path(st["file"]).name + (" (bottom side)" if st.get("flip") else ""))
            if np_ >= 2:
                _, _, err = heightmap.registration(reg)
                add(OK if abs(err) <= 1 else BAD, "STEP placed on plate",
                    f"2 points, lengths agree within {err:+.2f} mm")
            elif np_ == 1:
                add(OK, "STEP placed on plate", f"1 point + rotation {reg.get('rot_deg', 0)} deg")
            else:
                add(BAD, "STEP placed on plate", "not yet: Setup > Place STEP")
        else:
            add(INFO, "STEP model", "none (optional)")
        srcs = self.heights.sources if self.heights is not None else []
        add(OK if srcs else INFO, "Height source",
            "; ".join(srcs) if srcs else f"flat rule: tallest part {cfg['board']['tallest_part']} mm")
        cal = cfg["calibration"]
        t = cfgmod.load(TEMPLATE)["calibration"] if TEMPLATE.exists() else {}
        same = cal.get("origin_x") == t.get("origin_x") and cal.get("origin_y") == t.get("origin_y")
        add(BAD if same else OK, "Plate origin", "template value: set it (Setup > Machine)" if same
            else f"marker 0 at ({cal['origin_x']}, {cal['origin_y']}), angle {cal.get('angle_deg', 0)} deg")
        if not cfg["printer"].get("has_nozzle", True):
            add(OK if "z_board_top" in cal else BAD, "Board top Z",
                f"{cal['z_board_top']}" if "z_board_top" in cal else "not set: Setup > Machine > Set board top")
        sc = cfg["scan"]
        add(OK, "Scan area", f"X {sc['x_min']:g}..{sc['x_max']:g}  Y {sc['y_min']:g}..{sc['y_max']:g}, "
                             f"step {sc['step']:g}, heights {sc['heights']}")
        add(OK if not self.problems else BAD, "Plan",
            ("safe, " if not self.problems else "; ".join(self.problems)[:300] + "  ")
            + (f"{len(self.plan)} points, about {fmt_s(self.est)}" if self.plan else ""))
        m = self.machine
        add(OK if m.printer else INFO, "Printer", ("connected" + (", homed" if m.homed else ", NOT homed"))
            if m.printer else "not connected (Run connects it, or Prep > Connect printer)")
        add(OK if m.analyzer else INFO, "Analyzer", "connected" if m.analyzer else "not connected")

    def load_photo(self):
        if self.cfg is None:
            return
        f = filedialog.askopenfilename(title="Board photo (all 4 markers in view)",
                                       filetypes=[("Images", "*.jpg *.jpeg *.png *.heic *.bmp"), ("All", "*.*")])
        if not f:
            return
        cfg = copy.deepcopy(self.cfg)

        def work():
            import cv2

            from .photo import rectify, save_rectified
            img = cv2.imread(f)
            if img is None:
                raise RuntimeError(f"cannot read {f}")
            rect, ext, _ = rectify(cfg, img)
            out = Path(cfg["_path"]).with_name(Path(cfg["_path"]).stem + "_photo.png")
            save_rectified(rect, ext, out)
            return out

        def done(out):
            self.set_cfg("job", "photo_rectified", out.name, recompute=False)
            self.say(f"[photo] rectified: {out}")
            self.open_job(self.cfg_path)
        self.bg(work, done, "photo")

    def load_step(self):
        if self.cfg is None:
            return
        f = filedialog.askopenfilename(title="Board STEP model", initialdir=self.cfg["_dir"],
                                       filetypes=[("STEP", "*.step *.stp"), ("All", "*.*")])
        if not f:
            return
        try:
            rel = os.path.relpath(f, self.cfg["_dir"])
        except ValueError:
            rel = f
        flip = bool(self.v_flip.get())
        self.say(f"[step] loading {f} ...")
        cfg = copy.deepcopy(self.cfg)

        def work():
            return heightmap.load_step(f, flip, cache_dir=Path(cfg["_dir"]))

        def done(board):
            self.board, self._board_key = board, (rel, flip)
            w, d = board.size
            sc = self.cfg["scan"]
            old = self.setup.get("step") or {}
            self.setup["step"] = {"file": rel, "flip": flip,
                                  "reg": old.get("reg") or {"b": [[0, 0], [w, 0]], "p": [], "rot_deg": 0}}
            self.save_setup(recompute=False)
            self.say(f"[step] {board.n_parts} parts, PCB {w:.1f} x {d:.1f} mm, thickness "
                     f"{board.thickness:.2f} mm, tallest {board.tallest:.1f} mm above the PCB")
            if abs(board.thickness - self.cfg["board"]["thickness"]) > 0.15 and board.thickness == board.thickness:
                if messagebox.askyesno("Board thickness", f"The model's PCB is {board.thickness:.2f} mm "
                                       f"thick; the job says {self.cfg['board']['thickness']}. Use "
                                       f"{board.thickness:.2f}?"):
                    self.set_cfg("board", "thickness", round(board.thickness, 2), recompute=False)
            self.say("[step] next: Setup > Place STEP (two board corners and where they are on the plate)")
            self.recompute()
            self.nb.select(self.tab_setup)
            self.sub.select(self.sub_reg.tab)
        self.bg(work, done, "STEP load")

    def remove_step(self):
        if self.setup.get("step") and messagebox.askyesno("STEP", "Stop using the STEP model for this job?"):
            self.setup["step"] = None
            self.save_setup()

    def _flip_changed(self):
        st = self.setup.get("step")
        if st:
            st["flip"] = bool(self.v_flip.get())
            st["reg"] = {"b": [[0, 0], [0, 0]], "p": [], "rot_deg": 0}
            self.say("[step] side changed: place the model again (Setup > Place STEP)")
            self.save_setup()

    def _update_step_info(self):
        st = self.setup.get("step") or {}
        if st.get("file") and self.board is not None:
            b = self.board
            self.lbl_step.configure(text=f"{Path(st['file']).name}\n{b.n_parts} parts, PCB {b.size[0]:.1f} x "
                                         f"{b.size[1]:.1f} mm, {b.thickness:.2f} thick, tallest {b.tallest:.1f} mm")
            self.v_flip.set(bool(st.get("flip")))
        elif st.get("file"):
            self.lbl_step.configure(text=f"{Path(st['file']).name} (loading...)")
        else:
            self.lbl_step.configure(text="no STEP (optional)")
        self.lbl_photo.configure(text="rectified photo loaded" if self.photo is not None else "no photo")
        self._update_reg_panel()

    def marker_sheet(self):
        if self.cfg is None:
            return
        import matplotlib.style
        from .markers import make_sheet
        out = Path(self.cfg["_dir"])
        with matplotlib.style.context("default"):          # printed: white, not the dark theme
            sheet = make_sheet(self.cfg, out)
        for p in sheet:
            self.say(f"[markers] {p}")
        os.startfile(str(out))

    # ------------------------------------------------------------------ devices
    def _machine_label(self):
        m = self.machine
        p = "connected" + (" (homed)" if m.homed else " (not homed)") if m.printer else "not connected"
        a = "connected" if m.analyzer else "not connected"
        self.lbl_machine.configure(text=f"printer: {p}   analyzer: {a}")
        self._update_pos()

    def connect_printer(self, then=None):
        if self.cfg is None or self.machine.printer is not None:
            if then:
                then()
            return
        from .printers import make_printer
        cfg = self.cfg

        def work():
            p = make_printer(cfg)
            p.connect(); p.prepare()
            p.where()
            return p

        def done(p):
            self.machine.printer, self.machine.homed = p, cfg["printer"]["type"] == "sim"
            self.say("[printer] connected. Home before any absolute move (Setup > Machine > Home).")
            self._machine_label(); self.refresh_checks()
            if then:
                then()
        self.bg(work, done, "printer connect")

    def connect_analyzer(self, then=None):
        if self.cfg is None or self.machine.analyzer is not None:
            if then:
                then()
            return
        from .analyzers import make_analyzer
        cfg = self.cfg

        def work():
            a = make_analyzer(cfg); a.connect()
            return a

        def done(a):
            self.machine.analyzer = a
            self.say(f"[analyzer] connected: {a.info()}")
            self._machine_label(); self.refresh_checks()
            if then:
                then()
        self.bg(work, done, "analyzer connect")

    def disconnect(self):
        if self.scanning():
            messagebox.showwarning("Disconnect", "Stop the scan first.")
            return
        for dev in (self.machine.analyzer, self.machine.printer):
            if dev is not None:
                try:
                    dev.close()
                except Exception:                               # noqa: BLE001
                    pass
        self.machine.printer = self.machine.analyzer = None
        self.machine.homed = False
        self._machine_label(); self.refresh_checks()

    def analyzer_test(self):
        def go():
            an, cfg = self.machine.analyzer, self.cfg

            def work():
                with self.machine.lock:
                    for lna in (False, True):
                        an.set_lna(lna)
                        for band in cfg["analyzer"]["bands"]:
                            t = time.time()
                            f, lv = an.sweep(band, (0, 0, 10))
                            i = int(np.argmax(lv))
                            print(f"LNA {'on ' if lna else 'off'} {band['name']:>14s}: {len(lv)} pts in "
                                  f"{time.time() - t:4.2f} s, median {np.median(lv):6.1f} dBm, peak "
                                  f"{lv[i]:6.1f} dBm @ {f[i] / 1e6:8.2f} MHz")
            self.bg(work, None, "analyzer test")
        self.connect_analyzer(then=go)

    def scanning(self) -> bool:
        return self.scan_thread is not None and self.scan_thread.is_alive()

    def mach(self, fn, what, need_home=True, after=None):
        """Run a printer action in the background, one at a time, never during a scan."""
        m = self.machine
        if self.scanning():
            messagebox.showwarning(what, "A scan is running.")
            return
        if m.printer is None:
            messagebox.showinfo(what, "Connect the printer first (Prep > Connect printer, or "
                                      "the Connect button here).")
            return
        if need_home and not m.homed:
            messagebox.showwarning(what, "Home the printer first (it does not know where it is).")
            return
        if not m.lock.acquire(blocking=False):
            self.say(f"[machine] busy, {what} ignored")
            return

        def work():
            try:
                return fn(m.printer)
            finally:
                m.lock.release()

        def done(r):
            self._update_pos(); self.draw_probe()
            if after:
                after(r)
        self.bg(work, done, what)

    # ------------------------------------------------------------------ Setup tab
    def _build_setup(self):
        t = self.tab_setup
        top = ttk.Frame(t, padding=(6, 4)); top.pack(fill="x")
        ttk.Label(top, text="Click does:").pack(side="left")
        self.v_mode = tk.StringVar(value="look")
        for val, lab in (("look", "Nothing (pan/zoom)"), ("area", "Drag scan area"), ("box", "Drag box"),
                         ("mark", "Add mark"), ("p1", "Plate point 1"), ("p2", "Plate point 2"),
                         ("goto", "Send probe here")):
            ttk.Radiobutton(top, text=lab, value=val, variable=self.v_mode).pack(side="left", padx=3)
        ttk.Label(top, text="   Show:").pack(side="left")
        self.v_view = tk.StringVar(value="heights")
        for val, lab in (("heights", "Part heights"), ("req", "Lowest tip height"), ("photo", "Photo only")):
            ttk.Radiobutton(top, text=lab, value=val, variable=self.v_view,
                            command=self.draw_setup).pack(side="left", padx=3)
        self.v_showpath = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Path", variable=self.v_showpath, command=self.draw_setup).pack(side="left", padx=6)

        body = ttk.PanedWindow(t, orient="horizontal"); body.pack(fill="both", expand=True)
        cf = ttk.Frame(body); body.add(cf, weight=3)
        self.fig = Figure(figsize=(8, 6), dpi=96)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=cf)
        tb = NavigationToolbar2Tk(self.canvas, cf, pack_toolbar=False); tb.update()
        tb.pack(side="bottom", fill="x")
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("button_release_event", self._on_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self._cbar = None
        self._probe_art = []

        side = ttk.Frame(body, padding=4); body.add(side, weight=1)
        # Buttons and the plan summary are packed at the BOTTOM first, so they always keep
        # their room. Before (2026-11-22) they were packed last and a tall panel or a long
        # problem list pushed "3D preview" off the window. The panels above scroll instead.
        bf = ttk.Frame(side); bf.pack(side="bottom", fill="x")
        ttk.Button(bf, text="Recompute path", command=self.recompute).pack(side="left")
        ttk.Button(bf, text="3D preview", command=self.preview3d).pack(side="left", padx=4)
        self.lbl_stats = ttk.Label(side, text="", justify="left", wraplength=380)
        self.lbl_stats.pack(side="bottom", fill="x", pady=6)
        self.sub = ttk.Notebook(side); self.sub.pack(fill="both", expand=True)
        self.sub_hts = self._scroll_tab("Heights")
        self.sub_reg = self._scroll_tab("Place STEP")
        self.sub_mach = self._scroll_tab("Machine")
        self.sub_area = self._scroll_tab("Scan area")
        self._build_heights_panel(self.sub_hts)
        self._build_reg_panel(self.sub_reg)
        self._build_machine_panel(self.sub_mach)
        self._build_area_panel(self.sub_area)

    def _scroll_tab(self, title: str) -> ttk.Frame:
        """A Setup side tab whose content scrolls when it is taller than the window (the
        Machine tab's Calibrate and Teach sections sat below the bottom edge on a laptop)."""
        outer = ttk.Frame(self.sub)
        self.sub.add(outer, text=title)
        cv = tk.Canvas(outer, highlightthickness=0, bd=0, background=theme.BG)
        sb = ttk.Scrollbar(outer, orient="vertical", command=cv.yview)
        inner = ttk.Frame(cv, padding=6)
        win = cv.create_window((0, 0), window=inner, anchor="nw")
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>", lambda e: cv.itemconfigure(win, width=e.width))

        if not hasattr(self, "_scrollers"):
            self._scrollers = {}
            self.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self._scrollers[str(cv)] = (cv, inner)
        inner.tab = outer               # what Notebook.select() needs
        return inner

    def _on_wheel(self, e):
        """Scroll the side tab under the mouse (if any); the map keeps its own wheel zoom."""
        w = self.winfo_containing(e.x_root, e.y_root)
        while w is not None:
            hit = getattr(self, "_scrollers", {}).get(str(w))
            if hit:
                cv, inner = hit
                if inner.winfo_reqheight() > cv.winfo_height():
                    cv.yview_scroll(int(-e.delta / 120), "units")
                return
            w = w.master

    # Heights panel
    def _build_heights_panel(self, f):
        ttk.Label(f, text="Low-profile board (no STEP): one master height for everything",
                  wraplength=360, foreground=theme.DIM).pack(anchor="w")
        r = ttk.Frame(f); r.pack(fill="x", pady=2)
        ttk.Label(r, text="Master height = tallest part (mm)").pack(side="left")
        self.v_flat = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.v_flat, width=7); e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self._set_setup_num("flat_mm", self.v_flat))
        e.bind("<FocusOut>", lambda ev: self._set_setup_num("flat_mm", self.v_flat))
        r = ttk.Frame(f); r.pack(fill="x", pady=2)
        ttk.Label(r, text="Side margin around the probe (mm)").pack(side="left")
        self.v_margin = tk.StringVar()
        e = ttk.Entry(r, textvariable=self.v_margin, width=7); e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self._set_setup_num("margin_xy", self.v_margin))
        e.bind("<FocusOut>", lambda ev: self._set_setup_num("margin_xy", self.v_margin))
        ttk.Separator(f).pack(fill="x", pady=6)
        ttk.Label(f, text="Tall parts (taught marks and drawn boxes). Add with the click modes "
                          "above, or Machine > Mark tall part here.", wraplength=360, foreground=theme.DIM).pack(anchor="w")
        self.lb_parts = tk.Listbox(f, height=12, font=("Consolas", 9))
        self.lb_parts.pack(fill="both", expand=True, pady=4)
        r = ttk.Frame(f); r.pack(fill="x")
        ttk.Button(r, text="Delete selected", command=self._del_part).pack(side="left")
        ttk.Button(r, text="Edit height", command=self._edit_part).pack(side="left", padx=4)
        ttk.Button(r, text="Clear all", command=self._clear_parts).pack(side="left")

    def _set_setup_num(self, key, var):
        try:
            v = float(var.get())
        except ValueError:
            var.set(f"{self.setup.get(key, 0):g}")
            return
        if self.setup.get(key) != v:
            self.setup[key] = v
            self.save_setup()

    def _refresh_parts(self):
        lb = self.lb_parts
        lb.delete(0, "end")
        for m in self.setup.get("marks") or []:
            lb.insert("end", f"mark  ({m['x']:6.1f}, {m['y']:6.1f})  h {m['h']:5.1f}  r {m.get('r', 3):g}  {m.get('note', '')}")
        for b in self.setup.get("boxes") or []:
            lb.insert("end", f"box   X {b['x0']:.1f}..{b['x1']:.1f}  Y {b['y0']:.1f}..{b['y1']:.1f}  h {b['h']:5.1f}  {b.get('note', '')}")
        self.v_flat.set(f"{float(self.setup.get('flat_mm') or 0):g}")
        self.v_margin.set(f"{float(self.setup.get('margin_xy', 1.5)):g}")

    def _sel_part(self):
        sel = self.lb_parts.curselection()
        if not sel:
            return None, None
        i = sel[0]
        nm = len(self.setup.get("marks") or [])
        return ("marks", i) if i < nm else ("boxes", i - nm)

    def _del_part(self):
        kind, i = self._sel_part()
        if kind:
            del self.setup[kind][i]
            self.save_setup()

    def _edit_part(self):
        kind, i = self._sel_part()
        if kind:
            item = self.setup[kind][i]
            h = simpledialog.askfloat("Height", "Top of the part above the board (mm):",
                                      initialvalue=item["h"], minvalue=0, maxvalue=200, parent=self)
            if h is not None:
                item["h"] = h
                self.save_setup()

    def _clear_parts(self):
        if messagebox.askyesno("Clear", "Delete every mark and box?"):
            self.setup["marks"], self.setup["boxes"] = [], []
            self.save_setup()

    # Place STEP panel
    def _build_reg_panel(self, f):
        ttk.Label(f, text="Tell the app where the board sits on the plate: pick two board "
                          "points (corners work well) and show where each one is on the plate: "
                          "click it on the photo (click mode Plate point 1/2) or jog the probe "
                          "tip onto it and press Use probe position.", wraplength=360,
                  foreground=theme.DIM).pack(anchor="w")
        self.reg_fig = Figure(figsize=(3.6, 1.9), dpi=90)
        self.reg_ax = self.reg_fig.add_subplot(111)
        self.reg_canvas = FigureCanvasTkAgg(self.reg_fig, master=f)
        self.reg_canvas.get_tk_widget().pack(fill="x", pady=4)
        self.v_b = [tk.StringVar(value="front-left"), tk.StringVar(value="front-right")]
        self.v_p = [tk.StringVar(), tk.StringVar()]
        corners = ["front-left", "front-right", "back-right", "back-left"]
        for i in range(2):
            fr = ttk.LabelFrame(f, text=f"Point {i + 1}", padding=4); fr.pack(fill="x", pady=2)
            ttk.Label(fr, text="Board").grid(row=0, column=0, sticky="w")
            cb = ttk.Combobox(fr, textvariable=self.v_b[i], values=corners, width=12, state="readonly")
            cb.grid(row=0, column=1, sticky="w")
            cb.bind("<<ComboboxSelected>>", lambda e, k=i: self._reg_board(k))
            ttk.Label(fr, text="Plate x, y").grid(row=1, column=0, sticky="w")
            ent = ttk.Entry(fr, textvariable=self.v_p[i], width=16); ent.grid(row=1, column=1, sticky="w")
            ent.bind("<Return>", lambda e, k=i: self._reg_plate_typed(k))
            ttk.Button(fr, text="Use probe position", command=lambda k=i: self._reg_from_probe(k)).grid(row=1, column=2, padx=4)
            ttk.Button(fr, text="Click on map", command=lambda k=i: self.v_mode.set(f"p{k + 1}")).grid(row=0, column=2, padx=4)
        r = ttk.Frame(f); r.pack(fill="x", pady=4)
        ttk.Label(r, text="One point only: board turned").pack(side="left")
        self.v_rot = tk.StringVar(value="0")
        cb = ttk.Combobox(r, textvariable=self.v_rot, values=["0", "90", "180", "270"], width=5, state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._reg_rot())
        ttk.Label(r, text="deg").pack(side="left")
        ttk.Button(f, text="Fit scan area to the board (+3 mm)", command=self.fit_area).pack(anchor="w", pady=2)
        ttk.Button(f, text="Forget plate points", command=self._reg_clear).pack(anchor="w")
        self.lbl_reg = ttk.Label(f, text="", wraplength=360); self.lbl_reg.pack(anchor="w", pady=4)

    def _reg(self) -> dict | None:
        st = self.setup.get("step")
        if not st:
            return None
        st.setdefault("reg", {"b": [[0, 0], [0, 0]], "p": [], "rot_deg": 0})
        reg = st["reg"]
        while len(reg.setdefault("b", [])) < 2:
            reg["b"].append([0.0, 0.0])
        reg.setdefault("p", [])
        return reg

    def _reg_board(self, k):
        reg = self._reg()
        if reg is None or self.board is None:
            return
        reg["b"][k] = list(self.board.corners()[self.v_b[k].get()])
        self.save_setup()

    def _set_plate_point(self, k, x, y):
        reg = self._reg()
        if reg is None:
            messagebox.showinfo("Place STEP", "Load a STEP model first (Prep tab).")
            return
        if self.board is not None:
            reg["b"][k] = list(self.board.corners()[self.v_b[k].get()])
        p = reg["p"]
        if k > len(p):
            messagebox.showinfo("Place STEP", "Set plate point 1 first.")
            return
        val = [round(float(x), 2), round(float(y), 2)]
        other = p[1 - k] if k in (0, 1) and len(p) > 1 - k else None
        if other and math.hypot(val[0] - other[0], val[1] - other[1]) < 5.0:
            messagebox.showinfo("Place STEP",
                                f"Plate point {k + 1} is the same spot as point {2 - k} "
                                f"({val[0]:g}, {val[1]:g}). Jog the probe tip to the other "
                                f"board corner first, then press Use probe position.")
            return
        if k == len(p):
            p.append(val)
        else:
            p[k] = val
        self.say(f"[step] plate point {k + 1} = ({x:.2f}, {y:.2f}) for board {self.v_b[k].get()}")
        self.save_setup()

    def _reg_plate_typed(self, k):
        try:
            x, y = [float(v) for v in re.split(r"[,\s]+", self.v_p[k].get().strip()) if v]
        except ValueError:
            return
        self._set_plate_point(k, x, y)

    def _reg_from_probe(self, k):
        pos = self.machine.pos
        if pos is None or not self.machine.homed:
            messagebox.showinfo("Place STEP", "Connect and home the printer, then jog the probe "
                                              "tip onto the board point.")
            return
        x, y = geometry.nozzle_to_plate_xy(self.cfg, pos[0], pos[1])
        self._set_plate_point(k, x, y)

    def _reg_rot(self):
        reg = self._reg()
        if reg is not None:
            reg["rot_deg"] = int(self.v_rot.get())
            self.save_setup()

    def _reg_clear(self):
        reg = self._reg()
        if reg is not None:
            reg["p"] = []
            self.save_setup()

    def _update_reg_panel(self):
        st = self.setup.get("step") or {}
        reg = st.get("reg") or {}
        ax = self.reg_ax
        ax.clear(); ax.set_axis_off()
        if self.board is not None and st.get("file"):
            b = self.board
            w, d = b.size
            res = max(w, d) / 220
            H = heightmap.raster_max(b.V[b.F], -1, -1, int((w + 2) / res), int((d + 2) / res), res)
            H[~np.isfinite(H)] = np.nan
            ax.imshow(H, origin="lower", extent=[-1, -1 + H.shape[1] * res, -1, -1 + H.shape[0] * res],
                      cmap="viridis")
            for name, (cx, cy) in b.corners().items():
                ax.plot(cx, cy, "o", color="red" if name in (self.v_b[0].get(), self.v_b[1].get()) else "white", ms=5)
            for i, var in enumerate(self.v_b):
                cx, cy = b.corners()[var.get()]
                ax.annotate(str(i + 1), (cx, cy), color="red", fontsize=11, fontweight="bold",
                            xytext=(4, 4), textcoords="offset points")
            ax.set_title("STEP seen from above (front at the bottom)", fontsize=8)
        self.reg_canvas.draw_idle()
        bpts = reg.get("b") or []
        if self.board is not None:
            for i in range(2):
                if i < len(bpts):
                    for name, c in self.board.corners().items():
                        if np.allclose(c, bpts[i], atol=0.01):
                            self.v_b[i].set(name)
        p = reg.get("p") or []
        for i in range(2):
            self.v_p[i].set(f"{p[i][0]:g}, {p[i][1]:g}" if i < len(p) and p[i] else "")
        self.v_rot.set(str(reg.get("rot_deg", 0)))
        if not st.get("file"):
            txt = "No STEP model loaded."
        elif len(p) >= 2:
            th, tvec, err = heightmap.registration(reg)
            txt = (f"Placed: board turned {math.degrees(th):.1f} deg. The two points' spacing "
                   f"differs by {err:+.2f} mm" + ("  (OK)" if abs(err) <= 1 else "  - TOO MUCH: re-pick")
                   + ". With two points the angle comes from the points and the rotation box "
                   "is not used; Forget plate points to place with one point and the box.")
        elif len(p) == 1:
            txt = f"Placed with one point and {reg.get('rot_deg', 0)} deg. A second point checks the angle."
        else:
            txt = "Not placed yet: the height map ignores the STEP until you give plate point 1."
        self.lbl_reg.configure(text=txt)

    def fit_area(self):
        if self.board is None or not ((self.setup.get("step") or {}).get("reg") or {}).get("p"):
            messagebox.showinfo("Fit", "Place the STEP on the plate first.")
            return
        th, t, _ = heightmap.registration(self.setup["step"]["reg"])
        w, d = self.board.size
        c = np.array([[0, 0, 0], [w, 0, 0], [w, d, 0], [0, d, 0]], float)
        P = heightmap.board_to_plate(c, th, t)
        r = lambda v: round(v * 2) / 2
        for key, val in (("x_min", r(P[:, 0].min() - 3)), ("x_max", r(P[:, 0].max() + 3)),
                         ("y_min", r(P[:, 1].min() - 3)), ("y_max", r(P[:, 1].max() + 3))):
            cfgmod.set_value(self.cfg_path, "scan", key, float(val))
        self.reload_cfg(); self._load_fields(); self._view_lim = None
        self.recompute()

    # Machine panel
    def _build_machine_panel(self, f):
        r = ttk.Frame(f); r.pack(fill="x")
        ttk.Button(r, text="Connect", command=self.connect_printer).pack(side="left")
        ttk.Button(r, text="Home (G28)", command=self.home).pack(side="left", padx=4)
        ttk.Button(r, text="Lift to safe Z", command=self.lift).pack(side="left")
        self.lbl_pos = ttk.Label(f, text="position: unknown", font=("Consolas", 9), justify="left")
        self.lbl_pos.pack(anchor="w", pady=4)
        jf = ttk.LabelFrame(f, text="Jog (keys: arrows = X/Y, PgUp/PgDn = Z, when the map has focus)", padding=4)
        jf.pack(fill="x")
        self.v_step = tk.StringVar(value="1")
        sr = ttk.Frame(jf); sr.grid(row=0, column=0, columnspan=5, sticky="w")
        ttk.Label(sr, text="step mm").pack(side="left")
        for s in ("0.1", "0.5", "1", "5", "10"):
            ttk.Radiobutton(sr, text=s, value=s, variable=self.v_step).pack(side="left")
        B = lambda txt, dx, dy, dz, r_, c_: ttk.Button(jf, text=txt, width=9,
                                                       command=lambda: self.jog(dx, dy, dz)).grid(row=r_, column=c_, padx=2, pady=2)
        B("Y+ back", 0, 1, 0, 1, 1); B("X-", -1, 0, 0, 2, 0); B("X+", 1, 0, 0, 2, 2); B("Y- front", 0, -1, 0, 3, 1)
        B("Z+ up", 0, 0, 1, 1, 4); B("Z- down", 0, 0, -1, 3, 4)
        self.v_guard = tk.BooleanVar(value=True)
        ttk.Checkbutton(f, text="Guard X/Y jogs with the height map (untick to teach tall parts)",
                        variable=self.v_guard).pack(anchor="w", pady=2)
        cf = ttk.LabelFrame(f, text="Calibrate (probe tip on the spot, then press)", padding=4)
        cf.pack(fill="x", pady=4)
        self.v_marker = tk.StringVar(value="0")
        r = ttk.Frame(cf); r.pack(fill="x")
        ttk.Button(r, text="Go to marker", command=self.goto_marker).pack(side="left")
        ttk.Combobox(r, textvariable=self.v_marker, values=["0", "1", "2", "3"], width=3, state="readonly").pack(side="left", padx=4)
        ttk.Button(cf, text="Tip over marker 0 center: set plate origin", command=self.cal_origin).pack(fill="x", pady=1)
        ttk.Button(cf, text="Tip over marker 1 center: set plate angle", command=self.cal_angle).pack(fill="x", pady=1)
        ttk.Button(cf, text="Tip touching paper on the BOARD top: set board top", command=self.cal_board_top).pack(fill="x", pady=1)
        ttk.Button(cf, text="Nozzle on paper on the PLATE (A1 mini): set plate top", command=self.cal_plate_top).pack(fill="x", pady=1)
        tf = ttk.LabelFrame(f, text="Teach a tall part (tip just above its top, a paper's gap)", padding=4)
        tf.pack(fill="x", pady=4)
        r = ttk.Frame(tf); r.pack(fill="x")
        ttk.Label(r, text="radius mm").pack(side="left")
        self.v_mark_r = tk.StringVar(value="3")
        ttk.Entry(r, textvariable=self.v_mark_r, width=5).pack(side="left", padx=3)
        ttk.Label(r, text="note").pack(side="left")
        self.v_mark_note = tk.StringVar()
        ttk.Entry(r, textvariable=self.v_mark_note, width=12).pack(side="left", padx=3)
        ttk.Button(tf, text="Mark tall part here", command=self.mark_here).pack(fill="x", pady=2)
        ttk.Label(tf, text="Big parts: mark two or three corners. Each mark becomes a post of this "
                           "radius at this height; the app adds the probe ring around it.",
                  wraplength=360, foreground=theme.DIM).pack(anchor="w")

    def _tip_h(self, pos=None):
        """Probe tip height above the board top for a nozzle position."""
        pos = pos or self.machine.pos
        if pos is None:
            return None
        return pos[2] - geometry.board_top_z(self.cfg) + float(self.cfg["probe"]["offset_z"])

    def _update_pos(self):
        pos = self.machine.pos
        if pos is None or self.cfg is None:
            self.lbl_pos.configure(text="position: unknown" + ("" if self.machine.printer else " (not connected)"))
            return
        px, py = geometry.nozzle_to_plate_xy(self.cfg, pos[0], pos[1])
        h = self._tip_h(pos)
        self.lbl_pos.configure(text=f"printer X {pos[0]:7.2f}  Y {pos[1]:7.2f}  Z {pos[2]:7.2f}\n"
                                    f"plate   x {px:7.2f}  y {py:7.2f}   tip {h:6.2f} mm above board"
                                    + ("" if self.machine.homed else "\n(NOT homed: numbers are not real)"))

    def home(self):
        txt = ("Home all axes (G28)?\n\nZ homing drives the probe down to the Z switch height. "
               "Nothing tall may be under the probe at the home position (front-left on most "
               "printers). If unsure, take the board off, home, then put it back.")
        if self.machine.printer is None:
            self.connect_printer(then=self.home)
            return
        if not messagebox.askokcancel("Home", txt):
            return

        def act(p):
            p.home()
            p.where()

        def after(_):
            self.machine.homed = True
            self._machine_label(); self.refresh_checks()
        self.mach(act, "home", need_home=False, after=after)

    def lift(self):
        self.mach(lambda p: p.lift(), "lift", need_home=False)

    def _step(self):
        try:
            return float(self.v_step.get())
        except ValueError:
            return 1.0

    def jog(self, ux, uy, uz):
        if self.cfg is None:
            return
        s = self._step()
        dx, dy, dz = ux * s, uy * s, uz * s
        pos = self.machine.pos
        if pos is None:
            messagebox.showinfo("Jog", "Position unknown: connect and home first.")
            return
        cfg = self.cfg
        nz = pos[2] + dz
        if nz < float(cfg["printer"].get("z_min", -999)) - 1e-6:
            self.say(f"[jog] refused: Z {nz:.2f} is below the firmware limit z_min")
            return
        if "z_board_top" in cfg["calibration"] or cfg["printer"].get("has_nozzle", True):
            h = self._tip_h((pos[0], pos[1], nz))
            if h is not None and h < -0.3 and dz < 0:
                self.say(f"[jog] refused: the tip would be {h:.2f} mm into the board")
                return
        if (dx or dy) and self.v_guard.get() and self.heights is not None and self.machine.homed:
            px, py = geometry.nozzle_to_plate_xy(cfg, pos[0] + dx, pos[1] + dy)
            need = float(self.heights.req_at(px, py)) - float(cfg["scan"]["clearance"])
            h = self._tip_h(pos)
            if h is not None and h < need:
                self.say(f"[jog] refused: the probe ring would touch a part there (tip {h:.1f} mm, needs "
                         f"{need:.1f}). Lift first, or untick the guard to teach.")
                return
        self.mach(lambda p: p.jog(dx, dy, dz), "jog", need_home=False)

    def _on_key(self, ev):
        if self.nb.select() != str(self.tab_setup) or self.cfg is None:
            return
        w = self.focus_get()
        if isinstance(w, (tk.Entry, ttk.Entry, ttk.Combobox, tk.Text, tk.Listbox)):
            return
        k = {"Left": (-1, 0, 0), "Right": (1, 0, 0), "Up": (0, 1, 0), "Down": (0, -1, 0),
             "Prior": (0, 0, 1), "Next": (0, 0, -1)}.get(ev.keysym)
        if k:
            self.jog(*k)

    def _marker_xy(self, i):
        cal = self.cfg["calibration"]
        a = math.radians(cal.get("angle_deg", 0))
        px, py = geometry.marker_centers_plate(self.cfg)[i]
        return (cal["origin_x"] + math.cos(a) * px - math.sin(a) * py,
                cal["origin_y"] + math.sin(a) * px + math.cos(a) * py)

    def goto_marker(self):
        i = int(self.v_marker.get())
        x, y = self._marker_xy(i)
        cfg = self.cfg
        safe = float(cfg["printer"]["safe_z"])
        z = max(float(cfg["printer"].get("z_min", -999)), geometry.plate_top_z(cfg) + 3.0)
        z = min(z, safe)
        self.mach(lambda p: p.move_to(x, y, z, via_z=safe), f"go to marker {i}")

    def cal_origin(self):
        pos = self.machine.pos
        if pos is None or not self.machine.homed:
            return messagebox.showinfo("Calibrate", "Connect, home and jog the tip over marker 0 first.")
        self.set_cfg("calibration", "origin_x", round(pos[0], 2), recompute=False)
        self.set_cfg("calibration", "origin_y", round(pos[1], 2))
        self.say(f"[cal] plate origin = ({pos[0]:.2f}, {pos[1]:.2f})")

    def cal_angle(self):
        pos = self.machine.pos
        if pos is None or not self.machine.homed:
            return messagebox.showinfo("Calibrate", "Jog the tip over marker 1 first.")
        cal = self.cfg["calibration"]
        vx, vy = pos[0] - cal["origin_x"], pos[1] - cal["origin_y"]
        px, py = geometry.marker_centers_plate(self.cfg)[1]
        ang = round(math.degrees(math.atan2(vy, vx) - math.atan2(py, px)), 3)
        err = math.hypot(vx, vy) - math.hypot(px, py)
        self.set_cfg("calibration", "angle_deg", ang)
        self.say(f"[cal] plate angle = {ang} deg (length error {err:+.2f} mm; over 1 mm means a "
                 "marker or the alignment is off)")

    def cal_board_top(self):
        pos = self.machine.pos
        if pos is None or not self.machine.homed:
            return messagebox.showinfo("Calibrate", "Jog the tip down onto a paper strip on the board top first.")
        z = round(pos[2] + float(self.cfg["probe"]["offset_z"]), 2)
        self.set_cfg("calibration", "z_board_top", z)
        self.say(f"[cal] board top Z = {z}")

    def cal_plate_top(self):
        pos = self.machine.pos
        if pos is None or not self.machine.homed:
            return
        self.set_cfg("calibration", "z_plate_top", round(pos[2], 2))
        self.say(f"[cal] plate top Z = {pos[2]:.2f}")

    def mark_here(self):
        pos = self.machine.pos
        if pos is None or not self.machine.homed:
            return messagebox.showinfo("Teach", "Connect, home, and jog the tip just above the part top.")
        if "z_board_top" not in self.cfg["calibration"] and not self.cfg["printer"].get("has_nozzle", True):
            return messagebox.showinfo("Teach", "Set the board top first (Calibrate).")
        try:
            r = float(self.v_mark_r.get())
        except ValueError:
            r = 3.0
        x, y = geometry.nozzle_to_plate_xy(self.cfg, pos[0], pos[1])
        h = round(self._tip_h(pos), 2)
        self.setup.setdefault("marks", []).append({"x": round(x, 2), "y": round(y, 2), "h": h, "r": r,
                                                   "note": self.v_mark_note.get().strip()})
        self.say(f"[teach] mark ({x:.1f}, {y:.1f}) h {h} r {r}")
        self.save_setup()

    # Scan area panel
    def _build_area_panel(self, f):
        self._field(f, 0, "X min", "scan", "x_min", width=8)
        self._field(f, 1, "X max", "scan", "x_max", width=8)
        self._field(f, 2, "Y min", "scan", "y_min", width=8)
        self._field(f, 3, "Y max", "scan", "y_max", width=8)
        self._field(f, 4, "Step (mm)", "scan", "step", width=8)
        self._field(f, 5, "Heights (mm)", "scan", "heights", "list", 12, note="e.g. 3, 8")
        self._field(f, 6, "Clearance (mm)", "scan", "clearance", width=8)
        ttk.Label(f, text="Heights are the nominal probe tip height above the board top. Each "
                          "point rises only as far as needed to clear the parts around it.",
                  wraplength=340, foreground=theme.DIM).grid(row=7, column=0, columnspan=3, sticky="w", pady=6)

    # Canvas
    def draw_setup(self):
        if self.cfg is None:
            return
        fig = self.fig
        if self.ax.has_data():
            self._view_lim = (self.ax.get_xlim(), self.ax.get_ylim())
        fig.clf()
        ax = self.ax = fig.add_subplot(111)
        self._cbar = None
        if self.photo is not None:
            ax.imshow(self.photo[0], extent=self.photo[1])
        view = self.v_view.get()
        h = self.heights
        if h is not None and view != "photo":
            A = h.H.copy() if view == "heights" else h.req.copy()
            if view == "heights":
                A[A <= 0.05] = np.nan
            im = ax.imshow(A, origin="lower", extent=h.extent, cmap="viridis",
                           alpha=0.55 if self.photo is not None else 0.95)
            self._cbar = fig.colorbar(im, ax=ax, shrink=0.8, fraction=0.04, pad=0.02,
                                      label="part height (mm)" if view == "heights" else "lowest tip height (mm)")
        s = self.cfg["scan"]
        ax.add_patch(Rectangle((s["x_min"], s["y_min"]), s["x_max"] - s["x_min"], s["y_max"] - s["y_min"],
                               fill=False, ec="red", lw=1.2, ls="--"))
        if self.plan and self.v_showpath.get():
            first = [p for p in self.plan if p.iz == 0]
            xs = np.array([p.x for p in first]); ys = np.array([p.y for p in first])
            ht = np.array([p.ht for p in first]); rs = np.array([p.raised for p in first])
            ax.plot(xs, ys, "-", color="white", lw=0.4, alpha=0.6)
            sc = ax.scatter(xs, ys, c=ht, cmap="plasma", s=8, zorder=3)
            fig.colorbar(sc, ax=ax, shrink=0.8, fraction=0.04, pad=0.02,
                         label=f"tip height on the path (mm); {int(rs.sum())} raised")
        for m in self.setup.get("marks") or []:
            ax.add_patch(Circle((m["x"], m["y"]), m.get("r", 3), fill=False, ec="orange", lw=1.5))
            ax.annotate(f"{m['h']:g}", (m["x"], m["y"]), color="orange", fontsize=8, ha="center")
        for b in self.setup.get("boxes") or []:
            ax.add_patch(Rectangle((min(b["x0"], b["x1"]), min(b["y0"], b["y1"])), abs(b["x1"] - b["x0"]),
                                   abs(b["y1"] - b["y0"]), fill=False, ec="orange", lw=1.5))
            ax.annotate(f"{b['h']:g}", ((b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2), color="orange",
                        fontsize=8, ha="center")
        reg = ((self.setup.get("step") or {}).get("reg") or {})
        for i, p in enumerate(reg.get("p") or []):
            if p:
                ax.plot(p[0], p[1], "+", color="cyan", ms=14, mew=2)
                ax.annotate(f"P{i + 1}", p, color="cyan", xytext=(5, 5), textcoords="offset points")
        ax.set_aspect("equal")
        ax.set_xlabel("X plate (mm)   front of the printer is at the bottom"); ax.set_ylabel("Y plate (mm)")
        if self._view_lim:
            ax.set_xlim(*self._view_lim[0]); ax.set_ylim(*self._view_lim[1])
        else:
            pad = 15
            ax.set_xlim(s["x_min"] - pad, s["x_max"] + pad); ax.set_ylim(s["y_min"] - pad, s["y_max"] + pad)
        self._probe_art = []
        self.draw_probe(redraw=False)
        fig.tight_layout()
        self.canvas.draw_idle()
        self._refresh_parts()

    def draw_probe(self, redraw=True):
        for a in self._probe_art:
            try:
                a.remove()
            except ValueError:
                pass
        self._probe_art = []
        pos = self.machine.pos
        if pos is None or self.cfg is None or not self.machine.homed:
            if redraw:
                self.canvas.draw_idle()
            return
        x, y = geometry.nozzle_to_plate_xy(self.cfg, pos[0], pos[1])
        w = heightmap.PROBE_TIPS.get(str(self.cfg["probe"]["name"]).upper(), heightmap.PROBE_TIPS["H20"])["tip_w"]
        if self.cfg["probe"]["orientation"] == "x":
            seg = ([x - w / 2, x + w / 2], [y, y])
        else:
            seg = ([x, x], [y - w / 2, y + w / 2])
        self._probe_art += self.ax.plot(*seg, color="lime", lw=3, solid_capstyle="round", zorder=5)
        self._probe_art += self.ax.plot(x, y, "o", color="lime", ms=5, zorder=6)
        if redraw:
            self.canvas.draw_idle()

    def _nav_busy(self):
        return bool(self.ax.get_navigate_mode())

    def _on_press(self, ev):
        if ev.inaxes is not self.ax or self._nav_busy() or ev.xdata is None:
            return
        self.canvas.get_tk_widget().focus_set()
        mode = self.v_mode.get()
        if mode in ("area", "box"):
            self._drag = (ev.xdata, ev.ydata, self.ax.add_patch(
                Rectangle((ev.xdata, ev.ydata), 0, 0, fill=False, ec="yellow", lw=1.5)))
        elif mode == "mark":
            h = simpledialog.askfloat("Mark", f"Part top at ({ev.xdata:.1f}, {ev.ydata:.1f}): height "
                                      "above the board (mm)?", minvalue=0, maxvalue=200, parent=self)
            if h is None:
                return
            r = simpledialog.askfloat("Mark", "Radius of the part around this point (mm)?",
                                      initialvalue=3.0, minvalue=0.5, maxvalue=100, parent=self)
            self.setup.setdefault("marks", []).append({"x": round(ev.xdata, 2), "y": round(ev.ydata, 2),
                                                       "h": h, "r": r or 3.0, "note": "drawn"})
            self.save_setup()
        elif mode in ("p1", "p2"):
            self._set_plate_point(int(mode[1]) - 1, ev.xdata, ev.ydata)
            self.v_mode.set("look")
        elif mode == "goto":
            self.goto_plate(ev.xdata, ev.ydata)

    def _on_motion(self, ev):
        if self._drag and ev.inaxes is self.ax and ev.xdata is not None:
            x0, y0, r = self._drag
            r.set_xy((min(x0, ev.xdata), min(y0, ev.ydata)))
            r.set_width(abs(ev.xdata - x0)); r.set_height(abs(ev.ydata - y0))
            self.canvas.draw_idle()

    def _on_release(self, ev):
        if not self._drag:
            return
        x0, y0, rect = self._drag
        self._drag = None
        x1, y1 = (ev.xdata, ev.ydata) if ev.xdata is not None else (x0, y0)
        rect.remove()
        if abs(x1 - x0) < 1 or abs(y1 - y0) < 1:
            self.canvas.draw_idle()
            return
        xa, xb, ya, yb = sorted((x0, x1)) + sorted((y0, y1))
        if self.v_mode.get() == "area":
            r = lambda v: round(v * 2) / 2
            for key, val in (("x_min", r(xa)), ("x_max", r(xb)), ("y_min", r(ya)), ("y_max", r(yb))):
                cfgmod.set_value(self.cfg_path, "scan", key, float(val))
            self.reload_cfg(); self._load_fields()
            self.recompute()
        else:
            h = simpledialog.askfloat("Box", "Top of the parts in this box, above the board (mm)?",
                                      minvalue=0, maxvalue=200, parent=self)
            if h is None:
                self.canvas.draw_idle()
                return
            note = simpledialog.askstring("Box", "Note (optional, e.g. J1):", parent=self) or ""
            self.setup.setdefault("boxes", []).append({"x0": round(xa, 2), "x1": round(xb, 2),
                                                       "y0": round(ya, 2), "y1": round(yb, 2), "h": h,
                                                       "note": note})
            self.save_setup()

    def _in_map(self, x, y) -> bool:
        if self.heights is None:
            return False
        x0, x1, y0, y1 = self.heights.extent
        return x0 <= x <= x1 and y0 <= y <= y1

    def goto_plate(self, x, y):
        cfg = self.cfg
        if self.machine.pos is None or not self.machine.homed:
            messagebox.showinfo("Send probe", "Connect and home the printer first.")
            return
        h0 = float(cfg["scan"]["heights"][0])
        need = float(self.heights.req_at(x, y)) if self.heights is not None else geometry.min_height(cfg)
        h = max(h0, need + 0.05)
        nx, ny, nz = geometry.plate_to_nozzle(cfg, x, y, h)
        pos = self.machine.pos
        cx, cy = geometry.nozzle_to_plate_xy(cfg, pos[0], pos[1])
        if self.heights is not None and self._in_map(cx, cy) and self._in_map(x, y):
            via = geometry.plate_to_nozzle(cfg, x, y, self.heights.via(cx, cy, x, y) + 0.05)[2]
        else:
            # The height map only covers the scan area plus the probe's reach. Outside it the
            # map just repeats its edge, so it knows nothing there: cross at safe_z.
            via = float(cfg["printer"]["safe_z"])
        warn = ("\n\nWARNING: this spot is outside the height map (scan area plus probe reach), "
                "so the parts there are unknown. Make sure nothing taller than "
                f"{h:.1f} mm is under it." if self.heights is not None and not self._in_map(x, y) else "")
        if not messagebox.askokcancel("Send probe", f"Move the probe tip over ({x:.1f}, {y:.1f}) at "
                                      f"{h:.1f} mm above the board?{warn}"):
            return
        self.mach(lambda p: p.move_to(nx, ny, nz, via_z=via), "send probe")

    def preview3d(self):
        if self.cfg_path:
            self.run_cli(["preview", str(self.cfg_path)], "preview")

    # ------------------------------------------------------------------ Run tab
    def _build_run(self):
        t = self.tab_run
        left = ttk.Frame(t, padding=8); left.pack(side="left", fill="y")
        ttk.Label(left, text="Plan", style="Head.TLabel").pack(anchor="w")
        self.lbl_run_summary = ttk.Label(left, text="", justify="left", wraplength=380)
        self.lbl_run_summary.pack(anchor="w", pady=4)
        self.btn_start = ttk.Button(left, text="Start scan", style="Big.TButton", command=self.start_scan)
        self.btn_start.pack(fill="x", pady=3)
        self.btn_stop = ttk.Button(left, text="Stop (after this point)", command=self.stop_scan, state="disabled")
        self.btn_stop.pack(fill="x", pady=3)
        ttk.Button(left, text="Resume a stopped run...", command=self.resume_scan).pack(fill="x", pady=3)
        ttk.Button(left, text="Open live 3D view", command=self.live3d).pack(fill="x", pady=3)
        self.pb = ttk.Progressbar(left, length=360, mode="determinate"); self.pb.pack(fill="x", pady=8)
        self.lbl_prog = ttk.Label(left, text="idle", font=("Consolas", 10), justify="left")
        self.lbl_prog.pack(anchor="w")
        right = ttk.Frame(t, padding=8); right.pack(side="left", fill="both", expand=True)
        self.v_band = tk.StringVar(value="strongest")
        self.run_fig = Figure(figsize=(8, 6), dpi=96)
        self.run_ax = self.run_fig.add_subplot(111)
        self.run_canvas = FigureCanvasTkAgg(self.run_fig, master=right)
        self.run_canvas.get_tk_widget().pack(fill="both", expand=True)
        self._last_live_draw = 0.0

    def _confirm_start(self) -> bool:
        cfg = self.cfg
        pr = cfg["probe"]
        n_r = sum(p.raised for p in self.plan)
        msg = (f"Ready to scan {len(self.plan)} points (about {fmt_s(self.est)}).\n\n"
               f"  - Probe {pr['name']} in the mount, blade along {pr['orientation'].upper()}, caps clicked on\n"
               f"  - Board fixed down, nothing loose on the cover\n"
               f"  - Cables have slack for the whole travel (the bed moves)\n"
               f"  - Board powered and running the test firmware\n"
               f"  - {n_r} point(s) will be raised over tall parts\n\n"
               "Start?")
        return messagebox.askokcancel("Start scan", msg)

    def start_scan(self, resume: str | None = None):
        if self.cfg is None or self.scanning():
            return
        if self._done_gen != self._gen:
            messagebox.showwarning("Start scan", "The plan is still being computed, or the last "
                                                 "update failed. Wait for SAFE TO RUN (or press "
                                                 "Recompute path), then start.")
            return
        if self.problems:
            messagebox.showerror("Start scan", "The plan is not safe:\n- " + "\n- ".join(self.problems))
            return
        if not self.plan:
            return
        m = self.machine
        if m.printer is not None and not m.homed:
            messagebox.showwarning("Start scan", "Home the printer first (Setup > Machine).")
            return
        if m.printer is None and not messagebox.askyesno(
                "Start scan", "The printer is not connected in the app. The scan will connect "
                              "it itself and assume it is ALREADY HOMED. Continue?"):
            return
        if not resume and not self._confirm_start():
            return
        from .scan import run_scan
        cfg = copy.deepcopy(self.cfg)
        plan = self.plan
        self.scan_stop.clear()
        self.live_pts = []
        self.btn_start.configure(state="disabled"); self.btn_stop.configure(state="normal")

        def on_point(st):
            self.ui(self._on_point, st)

        def work():
            try:
                with m.lock:
                    run = run_scan(cfg, resume=resume, printer=m.printer,
                                   analyzer=m.analyzer, stop=self.scan_stop, on_point=on_point,
                                   log=self.say, plan=plan)
                self.ui(self._scan_done, run)
            except Exception as e:                              # noqa: BLE001
                self.say(f"[scan] error: {e}")
                traceback.print_exc()
                self.ui(self._scan_done, None)
                self.ui(messagebox.showerror, "Scan", str(e))
        self.scan_thread = threading.Thread(target=work, daemon=True)
        self.scan_thread.start()

    def stop_scan(self):
        self.scan_stop.set()
        self.say("[scan] stopping after the current point ...")

    def resume_scan(self):
        if self.cfg is None:
            return
        d = filedialog.askdirectory(title="Run folder to resume",
                                    initialdir=str(cfgmod.resolve(self.cfg, self.cfg["output"]["runs_dir"])))
        if d:
            self.start_scan(resume=d)

    def live3d(self):
        run = self.current_run or self._selected_run()
        if run:
            self.run_cli(["live", str(run)], "live")

    def _on_point(self, st):
        if "run" in st:
            self.current_run = Path(st["run"])
            return
        self.pb.configure(maximum=st["total"], value=st["done"])
        self.lbl_prog.configure(text=f"{st['done']}/{st['total']}  ETA {fmt_s(st.get('eta_s', 0))}\n"
                                     f"x {st['x']:.1f}  y {st['y']:.1f}  tip {st['ht']:.1f} mm"
                                     f"{'  RAISED' if st.get('raised') else ''}\n"
                                     f"peak {st['peak_dbm']:.1f} dBm @ {st['peak_hz'] / 1e6:.2f} MHz"
                                     f"   LNA {'on' if st.get('lna') else 'off'}"
                                     f"{'  OVERLOAD?' if st.get('overload') else ''}")
        self.live_pts.append((st["x"], st["y"], st["peak_dbm"]))
        if time.time() - self._last_live_draw > 1.0:
            self._draw_live()

    def _draw_live(self):
        self._last_live_draw = time.time()
        fig = self.run_fig
        fig.clf()
        ax = self.run_ax = fig.add_subplot(111)
        if self.photo is not None:
            ax.imshow(self.photo[0], extent=self.photo[1])
        if self.live_pts:
            a = np.array(self.live_pts)
            step = float(self.cfg["scan"]["step"])
            sc = ax.scatter(a[:, 0], a[:, 1], c=a[:, 2], cmap="inferno", s=(step * 6) ** 2 / 4,
                            marker="s", alpha=0.8)
            fig.colorbar(sc, ax=ax, shrink=0.8, label="peak dBm (all bands)")
        s = self.cfg["scan"]
        ax.set_xlim(s["x_min"] - 8, s["x_max"] + 8); ax.set_ylim(s["y_min"] - 8, s["y_max"] + 8)
        ax.set_aspect("equal"); ax.set_title("Live peak level")
        fig.tight_layout()
        self.run_canvas.draw_idle()

    def _scan_done(self, run):
        self.btn_start.configure(state="normal"); self.btn_stop.configure(state="disabled")
        self._draw_live()
        self._update_pos(); self.draw_probe()
        self.refresh_runs()
        if run:
            self.current_run = Path(run)
            if messagebox.askyesno("Scan", "Scan ended. Make the report now?"):
                self.nb.select(self.tab_res)
                self.make_report(Path(run))

    # ------------------------------------------------------------------ Results tab
    def _build_results(self):
        t = self.tab_res
        left = ttk.Frame(t, padding=8); left.pack(side="left", fill="y")
        cols = ("run", "state", "points", "raised")
        self.tv_runs = ttk.Treeview(left, columns=cols, show="headings", height=16, selectmode="extended")
        for c, w in zip(cols, (260, 70, 80, 60)):
            self.tv_runs.heading(c, text=c.capitalize()); self.tv_runs.column(c, width=w, anchor="w")
        self.tv_runs.pack(fill="y", expand=True)
        self.tv_runs.bind("<<TreeviewSelect>>", lambda e: self._show_run())
        bf = ttk.Frame(left); bf.pack(fill="x", pady=4)
        for txt, cmd in (("Make report", lambda: self.make_report(self._selected_run())),
                         ("Open report", self.open_report), ("Open folder", self.open_run_folder),
                         ("3D view", self.live3d), ("Compare 2 selected", self.compare_runs),
                         ("Refresh", self.refresh_runs)):
            ttk.Button(bf, text=txt, command=cmd).pack(fill="x", pady=1)
        self.lbl_runinfo = ttk.Label(left, text="", justify="left", wraplength=480)
        self.lbl_runinfo.pack(anchor="w", pady=4)
        right = ttk.Frame(t, padding=8); right.pack(side="left", fill="both", expand=True)
        r = ttk.Frame(right); r.pack(fill="x")
        ttk.Label(r, text="Picture").pack(side="left")
        self.v_img = tk.StringVar()
        self.cb_img = ttk.Combobox(r, textvariable=self.v_img, width=60, state="readonly")
        self.cb_img.pack(side="left", padx=6)
        self.cb_img.bind("<<ComboboxSelected>>", lambda e: self._show_img())
        self.res_fig = Figure(figsize=(8, 6), dpi=96)
        self.res_canvas = FigureCanvasTkAgg(self.res_fig, master=right)
        self.res_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _runs_dir(self) -> Path | None:
        if self.cfg is None:
            return None
        return cfgmod.resolve(self.cfg, self.cfg["output"]["runs_dir"]).resolve()

    def refresh_runs(self):
        tv = self.tv_runs
        tv.delete(*tv.get_children())
        d = self._runs_dir()
        if d is None or not d.exists():
            return
        for run in sorted(d.iterdir(), reverse=True):
            if not (run / "plan.csv").exists():
                continue
            try:
                meta = json.loads((run / "meta.json").read_text())
            except (OSError, ValueError):
                meta = {}
            n = sum(1 for _ in open(run / "points.csv")) - 1 if (run / "points.csv").exists() else 0
            tv.insert("", "end", iid=str(run), values=(run.name, meta.get("state", "?"),
                                                       f"{n}/{meta.get('n_points', '?')}", meta.get("n_raised", 0)))

    def _selected_run(self) -> Path | None:
        sel = self.tv_runs.selection()
        return Path(sel[0]) if sel else None

    def _show_run(self):
        run = self._selected_run()
        if run is None:
            return
        try:
            meta = json.loads((run / "meta.json").read_text())
        except (OSError, ValueError):
            meta = {}
        self.lbl_runinfo.configure(text=f"{run.name}\nstarted {meta.get('started', '?')}  ended "
                                        f"{meta.get('ended', '?')}\nprobe {meta.get('probe', {}).get('name')} "
                                        f"blade along {meta.get('probe', {}).get('orientation')}\n"
                                        f"heights: {'; '.join(meta.get('height_sources') or ['flat'])}")
        imgs = sorted((run / "report").glob("*.png")) if (run / "report").exists() else []
        self.cb_img.configure(values=[p.name for p in imgs])
        if imgs:
            first = next((p for p in imgs if p.name.startswith("map_")), imgs[0])
            self.v_img.set(first.name)
        else:
            self.v_img.set("")
        self._show_img()

    def _show_img(self):
        run = self._selected_run()
        fig = self.res_fig
        fig.clf()
        if run and self.v_img.get():
            import matplotlib.image as mpimg
            ax = fig.add_subplot(111)
            ax.imshow(mpimg.imread(run / "report" / self.v_img.get()))
            ax.set_axis_off()
        else:
            fig.text(0.5, 0.5, "No report yet: Make report", ha="center", va="center", fontsize=14, color="#777")
        self.res_canvas.draw_idle()

    def make_report(self, run: Path | None):
        if run is None:
            return messagebox.showinfo("Report", "Pick a run on the left.")
        self.run_cli(["report", str(run)], "report", done=lambda rc: (self.refresh_runs(),
                                                                       self.tv_runs.selection_set(str(run)),
                                                                       self._show_run()))

    def open_report(self):
        run = self._selected_run()
        if run and (run / "report" / "report.html").exists():
            os.startfile(str(run / "report" / "report.html"))
        else:
            messagebox.showinfo("Report", "No report for this run yet: Make report.")

    def open_run_folder(self):
        run = self._selected_run()
        if run:
            os.startfile(str(run))

    def compare_runs(self):
        sel = self.tv_runs.selection()
        if len(sel) != 2:
            return messagebox.showinfo("Compare", "Select exactly two runs (Ctrl+click): the older one is "
                                                  "'before', the newer one 'after'.")
        a, b = sorted(sel)
        self.run_cli(["compare", a, b], "compare")

    # ------------------------------------------------------------------ close
    def _on_close(self):
        if self.scanning():
            if not messagebox.askyesno("Quit", "A scan is running. Stop it and quit?"):
                return
            self.scan_stop.set()
            self.scan_thread.join(timeout=30)
        try:
            m = self.machine
            if m.printer is not None and m.homed:
                # Take the machine lock: a jog or Send probe still running in the background
                # would otherwise talk to the printer at the same time as the park.
                if m.lock.acquire(timeout=10):
                    try:
                        self.say("[printer] closing: lifting Z, then homing X and Y (Z stays)")
                        self.update_idletasks()
                        m.printer.park()
                    finally:
                        m.lock.release()
                else:
                    self.say("[printer] still busy after 10 s: closing without parking")
        except Exception:                                       # noqa: BLE001
            pass
        self.disconnect()
        self._closing = True
        sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
        self.destroy()


APP_ID = "Valence.EMCScanner"      # must match the shortcut made by tools\install_shortcut.ps1
ICON = Path(__file__).resolve().parent / "assets" / "emc_scanner.ico"


def _windows_identity():
    """Own taskbar button and icon. Without this Windows groups the window with any other
    app started by pythonw.exe (for example a pinned ClipBox) and shows that app's icon."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:                                           # noqa: BLE001
        pass


def main(job: str | None = None):
    _windows_identity()                     # before the first window exists
    app = App(job)
    if ICON.exists():
        try:
            app.iconbitmap(default=str(ICON))   # main window and every dialog
        except tk.TclError:
            pass
    app.mainloop()
