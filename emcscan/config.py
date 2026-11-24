"""Job configuration.

Every scan is described by ONE human-readable TOML file (see configs/). This module
loads it, fills in defaults for anything left out, and can write single values back
into the file (used by the calibrate and photo commands) without touching comments.

Read a value like this:   cfg["scan"]["step"]
"""
from __future__ import annotations

import copy
import re
import tomllib
from pathlib import Path

# Anything not in the TOML file falls back to these values.
# Units: millimeters, seconds, Hz, dBm.
DEFAULTS: dict = {
    "job": {
        "name": "job",
        "photo_rectified": "",      # set by `emcscan photo`
        "setup": "",                # height setup JSON (default: <job file name>_setup.json)
    },
    "plate": {                      # A5 bed cover (cad/a5_bed_cover.py). A1 mini: 150 x 150 x 12
        "width": 310.0,             # X, left to right
        "depth": 310.0,             # Y, front to back
        "height": 50.0,             # Z, nominal thickness
        "marker_size": 20.0,        # black square of each paper marker
        "marker_border": 3.0,       # white quiet zone around the black square
        "marker_inset": 4.0,        # gap from plate edge to marker pocket
        "pocket_depth": 0.4,        # recess for the paper label
    },
    "board": {
        "thickness": 1.6,           # PCB thickness
        "tallest_part": 3.0,        # tallest component above the board top surface
    },
    "probe": {
        "name": "H20",              # TBPS01: H20, H10, H5 or E5
        "orientation": "x",         # "x": loop plane along printer X, "y": rotated 90 degrees
        "offset_z": 0.0,            # probe tip ABOVE nozzle tip (mm). A5: 0, the tip IS the reference
        "tip_w": 0.0,               # widest tip seen from the side; 0 = TBPS01 table (heightmap.py)
        "head_h": 20.0,             # probe mount underside above the tip (A5 mount v4: 20.5)
        "head_r": 45.0,             # radius around the probe treated as mount and head
    },
    "printer": {
        "type": "sim",              # sim | bambu_mqtt | serial_gcode | moonraker
        "host": "",                 # bambu_mqtt: printer IP
        "serial": "",               # bambu_mqtt: printer serial number
        "access_code": "",          # bambu_mqtt: LAN access code from the printer screen
        "port": "",                 # serial_gcode: COM port
        "baud": 250000,             # JG Aurora A5, Marlin 1.1.8
        "url": "",                  # moonraker: http://<ip>:7125
        "bed_x": 300.0,             # travel limits, JG Aurora A5 (A1 mini: 180 x 180 x 150)
        "bed_y": 305.0,
        "z_max": 250.0,
        "z_min": 0.0,               # lowest Z the firmware accepts (A5 raised endstop: 0; A1 mini: -999)
        "has_nozzle": False,        # False: hotend removed, the probe tip is the lowest point (A5)
        "safe_z": 60.0,             # Z for travel moves and at the end (A1 mini: 40)
        "feed_xy": 3000.0,          # mm/min
        "feed_z": 600.0,            # mm/min
        "accel": 500.0,             # mm/s^2, kept low so the moving bed does not jolt the board
        "settle_s": 0.15,           # wait after each move before measuring
        "quiet_sweep": True,        # drivers off (M84) during each sweep: removes up to 28 dB of gantry noise (2026-11-24; default per Jacob)
        "quiet_settle_s": 0.1,      # wait after M84 before sweeping
    },
    "calibration": {                # written by `emcscan calibrate`
        "origin_x": 150.0,          # nozzle X when the PROBE TIP is over marker 0
        "origin_y": 150.0,          # nozzle Y when the PROBE TIP is over marker 0
        "angle_deg": 0.0,           # plate rotation on the bed (from markers 0 and 1)
        # "z_plate_top": nozzle Z touching the plate top; absent = use plate.height
        # "z_board_top": probe tip Z touching the board top (boards on pads); overrides
        #                z_plate_top + board.thickness
    },
    "scan": {                       # all in PLATE coordinates: origin = center of marker 0
        "x_min": 10.0, "x_max": 60.0,
        "y_min": 10.0, "y_max": 60.0,
        "step": 2.0,
        "heights": [6.0],           # probe tip height(s) above the board top surface
        "clearance": 1.0,           # extra margin above tallest_part
    },
    "analyzer": {
        "type": "sim",              # sim | tinysa
        "port": "auto",             # COM port, or auto-detect the TinySA by USB id
        "rbw_khz": "auto",
        "lna": "auto",              # auto | on | off  (TinySA Ultra built-in LNA)
        "lna_threshold_dbm": -40.0, # auto: LNA on only if the pre-scan peak is below this
        "overload_dbm": -30.0,      # with LNA on, readings above this are flagged
        "bands": [
            {"name": "30-300 MHz", "start_hz": 30e6, "stop_hz": 300e6, "points": 450},
            # split at 800 MHz: the TinySA Ultra noise floor steps down ~8 dB there
            {"name": "300-800 MHz", "start_hz": 300e6, "stop_hz": 800e6, "points": 450},
            {"name": "800-1000 MHz", "start_hz": 800e6, "stop_hz": 1000e6, "points": 180},
        ],
    },
    "sim": {                        # only used when printer/analyzer type = "sim"
        "time_scale": 0.0,          # 0 = instant, 1 = real time, 0.05 = 20x faster
        "noise_floor_dbm": -95.0,
        "sources": [],
    },
    "output": {
        "runs_dir": "runs",
    },
}


def _merge(base: dict, override: dict) -> dict:
    """Deep-merge dicts. Lists and plain values in `override` replace the default."""
    out = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def load(path: str | Path) -> dict:
    path = Path(path)
    with open(path, "rb") as fh:
        user = tomllib.load(fh)
    cfg = _merge(DEFAULTS, user)
    cfg["_path"] = str(path.resolve())
    cfg["_dir"] = str(path.resolve().parent)
    return cfg


def resolve(cfg: dict, rel: str) -> Path:
    """Paths in the config are relative to the config file's folder."""
    p = Path(rel)
    return p if p.is_absolute() else Path(cfg["_dir"]) / p


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") if value != int(value) else f"{value:.1f}"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    return '"' + str(value).replace("\\", "/") + '"'


def set_value(path: str | Path, section: str, key: str, value) -> None:
    """Write `key = value` into `[section]` of a TOML file, keeping all comments.

    Replaces the existing line if present, otherwise adds it right under the section
    header (and adds the section at the end if it does not exist).
    """
    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    # [[array.tables]] (the analyzer bands) end the section before them too; without the
    # optional second bracket a key missing from [analyzer] could land in a band's table.
    header = re.compile(r"^\s*\[(\[?)([^\[\]]+)\]\]?\s*(#.*)?$")
    keyline = re.compile(rf"^\s*{re.escape(key)}\s*=")
    start = end = None
    for i, line in enumerate(lines):
        m = header.match(line)
        if m:
            if start is not None and end is None:
                end = i
            if not m.group(1) and m.group(2).strip() == section:
                start = i
    if start is None:
        lines += ["", f"[{section}]", f"{key} = {_fmt(value)}"]
    else:
        end = len(lines) if end is None else end
        for i in range(start + 1, end):
            if keyline.match(lines[i]):
                comment = ""
                if "#" in lines[i]:
                    comment = "  #" + lines[i].split("#", 1)[1]
                lines[i] = f"{key} = {_fmt(value)}{comment}"
                break
        else:
            lines.insert(start + 1, f"{key} = {_fmt(value)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
