"""emcscan: Valence near-field EMC scanner (printer gantry + TinySA Ultra).

Module map (read in this order to understand the pipeline):
    config.py     job file (TOML) and defaults
    geometry.py   plate / printer coordinate frames
    plan.py       the raster of points, safety checks, time estimate
    printers.py   printer drivers (Bambu MQTT, serial G-code, Moonraker, simulator)
    analyzers.py  TinySA Ultra driver and a simulated board
    runstore.py   run folder format (plain CSV / numpy files)
    scan.py       the scan loop
    live.py       live 3D viewer
    photo.py      board photo -> plate coordinates via the 4 markers
    report.py     heat maps, hotspots, spectra, report.html
    compare.py    before / after
    bench.py      home, calibrate, goto, device tests
    cad.py        printable parts;  markers.py  paper marker sheet
    cli.py        the `emcscan` command
"""
__version__ = "0.1.0"
