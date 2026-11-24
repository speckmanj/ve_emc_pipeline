# EMC Scanner app

Start: double-click `EMC Scanner.bat` in `C:\work\ve_emc_pipeline` (or `emcscan app [job.toml]`).
It reopens the last job. Everything below also works from the command line (`emcscan -h`).

**Privacy.** The app is plain local Python. No AI, no internet, no telemetry. STEP models,
photos and results are read and written only in the job folder
(`C:\work\ve_emc_jobs\<YYYY-MM>_<Customer>_<Device>\04_scan\`). STEP files are ignored by
git, so they cannot be pushed by accident.

## 1 Prep

1. **New job...**: customer + device. Creates `...\04_scan\scan.toml` from the A5 template.
2. Check probe (H20/H10/H5/E5) and **Blade along** (x reads Hy, y reads Hx). This must match
   how the probe sits in the mount.
3. **Load board photo...** (all four cover markers in view): flattened to plate millimeters.
4. **Load STEP model...** (optional, from `00_intake`). Tick **Scan the BOTTOM side** first
   if the board is upside down. The app reads the PCB thickness from the model and offers it.
5. **Connect printer / analyzer**, **Analyzer test sweep**.
6. The **Checklist** on the right turns green as each item is done.

## 2 Setup

The map shows the plate from above (front of the printer at the bottom). **Show:** part
heights, the lowest legal tip height, or the photo only. The path is colored by tip height.

| Task | How |
|---|---|
| Place the STEP | **Place STEP** tab: pick two board corners, then show where each is on the plate (click mode **Plate point 1/2** on the photo, or jog the tip onto the corner and **Use probe position**). The spacing check should read within 1 mm. **Fit scan area to the board** |
| Scan area by hand | Click mode **Drag scan area**, or type it in the **Scan area** tab |
| Low-profile board, no STEP | **Heights** tab: master height = tallest part |
| Tall parts, no STEP (or extras: cables, nylon screws, heatsinks) | **Machine** tab: untick the guard, jog the tip a paper's gap above the part top, **Mark tall part here** (two or three corners for big parts). Or click mode **Add mark** / **Drag box** and type the height |
| Calibrate | **Machine** tab: Home, then tip over marker 3 (back-left, 4 dimples), then marker 2 (back-right, 3 dimples): plate cal, saved as the machine default for every job (redo only if the cover moves). Then tip on paper on the board top (board top, per job) |
| Check | Stats under the tabs say SAFE TO RUN or list the problems. **3D preview** shows the board, tall parts and the path |

Keys on the map (click it first): arrows jog X/Y, PgUp/PgDn jog Z, at the chosen step.
With the guard on, X/Y jogs that would put the ring into a part are refused.

## 3 Run

**Start scan** asks for the bench checklist, then runs. **Stop** finishes the current point
and lifts. **Resume a stopped run...** continues where it stopped. **Open live 3D view**
for the stacked 3D window.

## 4 Results

Pick a run: **Make report**, **Open report** (HTML, prints to PDF), pictures in the viewer,
**Compare 2 selected** (older = before), **3D view**.

## How the height map works (short)

- Sources: STEP (every part, exact), marks, boxes, master height; the highest wins.
- The probe hangs straight down, so from above the H20 ring is a 24 x 2 mm line, not a disc,
  and its bottom is round: 8 mm from the center it is already 3 mm up. The app slides that
  shape over the map for the chosen blade direction, adds the clearance, and also keeps the
  probe mount (20 mm above the tip, 45 mm around) clear of anything very tall.
- Each point: tip height = max(nominal height, lowest legal height there).
- Each move: climb to the highest legal height along the line, cross, drop.
- Raised points are marked x on the heat maps: they read weaker than their neighbours.
- Ring sizes: H20 measured (23.8 x 24). H10 is an estimate (14 mm): measure it before
  trusting H10 scans near tall parts (`heightmap.PROBE_TIPS`).
