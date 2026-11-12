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

## Try it with no hardware

```powershell
emcscan demo                      # simulated board: scan, report, "fix", before/after
emcscan demo --live               # same, slowed down, with the live 3D window
python tests\run_tests.py         # self-test (7 checks)
```

Results land in `runs\`. Open `runs\<run>\report\report.html` in a browser.

## A real job, start to finish

| Step | Command | What it does |
|---|---|---|
| 1 | `emcscan stl` and `emcscan markers` | Printable parts and the paper marker sheet, into `cad\out\` (one time) |
| 2 | copy `configs\a1mini_tinysa.toml` to `jobs\<customer>_<board>.toml` | One job file per board; edit IP, serial, access code, board height |
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
| `configs\` | job template (`a1mini_tinysa.toml`) and the simulator demo (`demo_sim.toml`) |
| `jobs\` | your job files and rectified board photos |
| `cad\out\` | STL parts and the marker sheet |
| `runs\` | one folder per scan (not in git) |
| `tests\` | `run_tests.py` self-test |
| `docs\` | workflow and architecture notes |

## Hardware

Bambu A1 mini (LAN Only + Developer Mode), TinySA Ultra (built-in LNA), TekBox TBPS01
probes with their 75 cm SMB-to-SMA cable. Other printers work by changing `printer.type`:
`serial_gcode` (Marlin/Prusa over USB), `moonraker` (Klipper, e.g. Sovol SV08).
