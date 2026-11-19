# emcscan: Valence near-field EMC scanner

A printer moves a TekBox TBPS01 near-field probe over a board in a grid. The TinySA
Ultra sweeps the spectrum at every point. You watch it build up live in 3D. At the end
you get heat maps over a photo of the board, a hotspot table and a one-file HTML report.
A before/after comparison shows whether a fix worked.

Everything runs without AI. Every file it writes is plain CSV, JSON, numpy or PNG, so
you can open any of it yourself.

> A near-field scan shows **where** emissions come from and **how much a fix changed
> them**. It is not a compliance measurement and does not predict a pass or fail.

## Install (once)

```powershell
cd C:\work\ve_emc_pipeline
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\activate            # then just type: emcscan ...
```

## The app (recommended)

Double-click **`EMC Scanner.bat`** (or `emcscan app`). One window, four tabs, in the order
you work. It runs on this PC only: no AI, no internet, no cloud; customer STEP files and
photos stay in the job folder. Full guide: [docs/APP.md](docs/APP.md).

| Tab | What you do there |
|---|---|
| 1 Prep | New job / Open job, probe and machine settings, load the board photo and STEP model, device tests, checklist of what is missing |
| 2 Setup | Board from above with part heights and the probe path colored by tip height. Place the STEP on the plate, set the scan area, teach tall parts by jogging (or draw boxes, or one master height), calibrate, 3D preview |
| 3 Run | Start, stop, resume; live progress and peak map |
| 4 Results | Report, heat maps, before/after comparison, 3D view |

**Heights follow the board.** With a STEP model (or taught marks, boxes, or a master
height) each point flies only as high as the parts around it need, including the full
width of the H20 ring, and travel moves climb over anything in the way. See
`emcscan\heightmap.py`.

## Try it with no hardware

```powershell
emcscan demo                      # simulated board: scan, report, "fix", before/after
emcscan demo --live               # same, slowed down, with the live 3D window
python tests\run_tests.py         # self-test (9 checks)
```

Results land in `runs\`. Open `runs\<run>\report\report.html` in a browser.

## A real job, start to finish

| Step | Command | What it does |
|---|---|---|
| 1 | `emcscan stl` and `emcscan markers` | A5 parts (bed cover, fixture kit, probe mount, dummy probes, Z spacer, fit tests) and the paper marker sheet, into `cad\out\` (one time). `--a1mini` builds the old A1 mini parts |
| 2 | copy `configs\a5_tinysa.toml` to `jobs\<customer>_<board>.toml` (the app does this for you) | One job file per board; check the COM port (Device Manager, CH340) and board height |
| 3 | `emcscan printer-test jobs\x.toml` / `emcscan analyzer-test jobs\x.toml` | Check both devices answer (no motion) |
| 4 | `emcscan home jobs\x.toml` | Home with the bed EMPTY, then place plate and board |
| 5 | `emcscan calibrate jobs\x.toml` | Jog the probe over marker 0 and 1, touch-off the plate top, `save` |
| 6 | `emcscan photo jobs\x.toml board.jpg --pick` | Flatten the phone photo; click two corners to set the scan area |
| 7 | `emcscan plan jobs\x.toml --show` | Safety check, point count, time estimate, 3D path preview |
| 8 | `emcscan scan jobs\x.toml --live` | Scan with the live viewer. Ctrl+C stops safely; `--resume <run>` continues |
| 9 | `emcscan report runs\<run> --open` | Heat maps, hotspots, spectra, report.html |
| 10 | `emcscan compare runs\<before> runs\<after>` | Before/after maps and difference |
| any | `emcscan goto jobs\x.toml X Y H` | Park the probe on a hotspot and watch the TinySA by hand |

Details and bench checklists are in [docs/WORKFLOW.md](docs/WORKFLOW.md); how the code fits
together is in [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Folders

| Folder | Holds |
|---|---|
| `emcscan\` | the Python package (one file per job step, see `emcscan\__init__.py`) |
| `configs\` | job templates (`a5_tinysa.toml` for the A5, `a1mini_tinysa.toml`) and the simulator demo (`demo_sim.toml`) |
| `jobs\` | your job files and rectified board photos |
| `cad\out\` | STL parts and the marker sheet |
| `runs\` | one folder per scan (not in git) |
| `tests\` | `run_tests.py` self-test |
| `docs\` | workflow and architecture notes |

## Hardware

JG Aurora A5 (hotend removed, Marlin 1.1.8 over USB at 250000 baud) is the default gantry. The Bambu A1 mini
(`configs\a1mini_tinysa.toml`, LAN Only + Developer Mode) still works. TinySA Ultra (built-in LNA), TekBox TBPS01
probes with their 75 cm SMB-to-SMA cable. Other printers work by changing `printer.type`:
`serial_gcode` (Marlin/Prusa over USB), `moonraker` (Klipper, e.g. Sovol SV08).
