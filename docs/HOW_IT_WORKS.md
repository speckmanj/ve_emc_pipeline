# How it works

## The data flow

```
job.toml ──> plan.py ──> scan.py ──────────────> run folder ──> report.py ──> report.html
  (what)     (where)       │  move  (printers.py)    │   │                    heat maps
                           │  sweep (analyzers.py)   │   └──> live.py (3D, reads every second)
                           └─ status.json ───────────┘
board.jpg ──> photo.py ──> rectified photo (plate mm) ──> under every map and 3D view
two runs ──> compare.py ──> compare.html
```

Each box is one file in `emcscan/`. Nothing talks to anything else except through files,
so the viewer can crash or close without touching the scan, and any run can be re-reported
later.

## Three coordinate frames (geometry.py)

- **Plate frame**: what you think in. Millimeters on the printed plate, origin at the center
  of marker 0 (front-left), X right, Y to the back, h = probe height above the board top.
  Scan areas, maps and reports use this.
- **Printer frame**: what the printer is told (nozzle X, Y, Z).
- **Photo**: pixels, mapped to the plate frame by the four markers.

`calibrate` measures the link between plate and printer (where marker 0 is, how the plate is
turned, and the plate top height). After that:

```
nozzle X, Y = origin + rotate(angle) * (plate x, y)
nozzle Z    = plate top + board thickness + h - probe offset_z
```

## Safety (plan.py, check_plan)

A scan will not start unless:
- every height is at least `tallest_part + clearance + offset_z` (the nozzle is the lowest
  point when the probe sits above it, so the nozzle must clear the parts too),
- all nozzle positions stay inside the bed limits and below `z_max`,
- `safe_z` is above every scan height, and the area is on the plate.

Moves go up first, then across, then down, so the nozzle never cuts diagonally into the board.

## The scan loop (scan.py)

1. LNA decision: 5 x 5 pre-scan with the LNA off. LNA on only if the strongest signal is
   below `lna_threshold_dbm` (the LNA overloads at about -26 dBm input).
2. For each point: move, settle (`settle_s`), one `scan` command per band on the TinySA,
   append the sweep to `traces_b<k>.f32`, then a line to `points.csv`, then rewrite
   `status.json`.
3. Ctrl+C or an error: lift to `safe_z`, mark the state. `--resume` skips points already
   in `points.csv`.

## Reading a run yourself

```python
from emcscan.runstore import Run
r = Run(r"runs\20261112-101500_customer_board")
r.freqs[0]           # Hz, band 0
r.traces[0]          # dBm, one row per measured point
r.peak_grid(0)       # [height, row, col] peak level map
r.freq_grid(0, 48e6) # level at 48 MHz at every point
```

Or `numpy.load("report/scan.npz")`.

## Printers

| type | Move finished? | Notes |
|---|---|---|
| `bambu_mqtt` | Waits computed travel time * 1.2 + 0.4 s | No completion report on Bambu's channel. Verify with bench step 5 |
| `serial_gcode` | Exact (M400 then "ok") | Marlin, Prusa |
| `moonraker` | Exact (HTTP returns after M400) | Klipper, SV08 |
| `sim` | Instant or scaled | Demos and tests |

## What is verified and what is not

| Part | Status |
|---|---|
| Plan, safety checks, run files, resume, report, compare, 3D views | Tested end to end with the simulator (`tests/run_tests.py`) |
| Photo registration | Tested on a synthetic tilted photo: 0.24 mm worst error |
| TinySA command parsing | Tested against a fake serial port; needs the real unit (bench step 2) |
| Bambu MQTT control | Written from the published protocol; needs the real printer (bench steps 1, 3 to 5) |
| STL fit | Probe slot uses guessed shaft size; measure and rebuild |
