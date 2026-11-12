# Workflow and bench checklists

## 0. Print the parts (once)

| Part | File | Settings | Notes |
|---|---|---|---|
| Spacer plate | `cad/out/spacer_plate.stl` | PLA or PETG, 0.2 mm layers, 2 walls, 10 % infill, 4 top layers | 150 x 150 x 12 mm. Arrow at the front edge points to the printer front |
| Probe mount | `probe_mount_x.stl` (and `_y`) | 0.2 mm, 4 walls, 30 % infill | Flat back down. Two M3 x 10 screws (nylon preferred) self-tap into the pilot holes |
| Height gauge | `probe_gauge.stl` | 0.2 mm layers | Steps 2 / 3 / 4 / 5 mm (that many dimples). Check with calipers |
| Marker sheet | `cad/out/markers_letter.pdf` | Paper, print at **100 % / Actual size** | Check the 50 mm bar. Cut, glue-stick into pockets: ID 0 front-left (1 dimple), 1 front-right (2), 2 back-right (3), 3 back-left (4) |

**Measure first:** the probe slot is sized for a 12.0 x 1.6 mm shaft (a guess). Measure
your TBPS01 shaft with calipers, put the numbers in a `cad.toml`:

```toml
[cad]
probe_w = 12.0
probe_t = 1.6
```

and rebuild with `emcscan stl --cad cad.toml`.

## 1. Mount the probe (A1 mini)

1. Stick the probe mount to the flat front of the toolhead with a removable adhesive strip
   (3M Command strip). Keep it clear of the fan intakes and the hotend release.
2. Home the printer with the probe slid UP out of the way (`emcscan home`).
3. Lower the nozzle onto a strip of paper on the plate top (`emcscan calibrate`, then `z -0.1`
   steps) until the paper just drags.
4. Put the gauge on the plate under the probe, slide the probe down onto the 3 mm step,
   tighten the screws. Now `probe.offset_z = 3.0` (use your caliper reading of that step).

## 2. First bring-up (do these once, in order)

| # | Setup | Look for | Meaning |
|---|---|---|---|
| 1 | Printer: LAN Only ON, Developer Mode ON. Fill host, serial, access_code in the job file. Run `emcscan printer-test jobs\x.toml` | `state=IDLE` (or similar) and a nozzle temperature | MQTT works. "no status in 15 s" = wrong IP, serial or access code, or a mode is off |
| 2 | TinySA on USB, switched on. `emcscan analyzer-test jobs\x.toml` | 450 points per band, LNA-on median about 15 dB below LNA-off | Serial works and the LNA switches. "not found on USB" = set `analyzer.port` to the COM port |
| 3 | Bed EMPTY, probe tip above nozzle. `emcscan home jobs\x.toml` | Normal homing, then park at Z 40 in the middle | G-code over MQTT works. If nothing moves, firmware is blocking gcode_line |
| 4 | `emcscan goto` is not safe yet (no calibration). Run `emcscan calibrate`, type `x 10` then `x -10` | Toolhead moves 10 mm right, then back | Jog direction and scale are right |
| 5 | In calibrate, time a 100 mm move: `x 100` | Prompt returns only after the move ends | The time-based wait is long enough. If the prompt returns early, raise the margin in `printers.py` (`t * 1.2 + 0.4`) |
| 6 | Plate taped to the bed, markers in. Calibrate: `m 0`, jog until the probe tip is over marker 0 center, `origin`; `m 1`, jog, `angle`; jog off-board, paper under nozzle, `z -0.1` until it drags, `ztop`; `save` | `length error` under 1 mm when saving the angle | Plate placement is known. Over 1 mm = a marker is in the wrong pocket or the jog was off |
| 7 | `m 2` and `m 3` in calibrate | Probe lands within about 1 mm of those marker centers | Calibration is good across the whole plate |
| 8 | Board taped on the plate, unpowered. `emcscan photo jobs\x.toml board.jpg --pick`, then `emcscan plan jobs\x.toml --show` | "SAFE TO RUN", path over the board in the preview | Area and heights are legal |
| 9 | Board UNPOWERED. `emcscan scan jobs\x.toml --live` | A flat, quiet map near the noise floor | This is the background: printer motors and room noise. Keep it for `compare` |
| 10 | Same job, board POWERED, scan again | Clear hot spots well above the background | The setup works |
| 11 | Run step 10 twice, then `emcscan compare runs\<a> runs\<b>` | Difference within about 1 to 2 dB everywhere | Repeatable: the moving bed is not disturbing the board or cables |

Safety: never home with a board on the bed; keep a hand near the power switch for the
first few moves; Ctrl+C in the scan window stops and lifts the nozzle.

## 3. A customer job

1. Copy the template to `jobs\<customer>_<board>.toml`; set `board.tallest_part` with calipers.
2. Home (empty bed), place plate and board, `calibrate` (or reuse if the plate did not move), `save`.
3. Photo from above with all four markers visible, then `emcscan photo ... --pick`.
4. `emcscan plan ... --show`; adjust `scan.step` (2 mm normal, 1 mm detail) and `heights`.
5. Background scan (unpowered), then the powered scan, both with `--live`.
6. `emcscan report runs\<powered> --open`; `emcscan compare runs\<background> runs\<powered>`.
7. Fix, rescan with the same job file, `emcscan compare runs\<before> runs\<after>`.
8. Send `report.html` (print to PDF from the browser) and the compare page.

## Tips

- Probe choice: H20 finds sources fast, H10 is the everyday probe, H5 pins a pin or trace,
  E5 for high-impedance nodes (clocks, long traces).
- Do a second scan with `probe_mount_y` and `probe.orientation = "y"`: a loop only sees the
  field through its face, so each orientation sees different currents.
- Overload flag in the report: rescan with `analyzer.lna = "off"`.
- Long scans: 1 mm steps quadruple the points; use them only over a hotspot area.
