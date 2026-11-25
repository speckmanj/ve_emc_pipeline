"""JG Aurora A5 probe mount for the TekBox TBPS01 near-field probes (H20, H10, H5, E5).

    .venv\\Scripts\\python cad\\a5_probe_mount.py
    .venv\\Scripts\\python cad\\a5_probe_mount.py --flange-t 3.2 --handle-d 14     (adds a lip over the flange)

v6 (2026-11-24): the probe hangs 20 mm lower (--tip-z -20). On the HIFIS main board the head
and the v5 mount (underside 20.5 mm above the tip) hit the tall bulk capacitors with the tip on
the board (Jacob's photos). The lower guide now never sits below the side leg's bottom edge
(--guide-floor, default on), so the whole mount stays as high as the head and the lowest
point of mount + head is 40.5 mm above the tip (was 20.5). The blade span between the guide
and the seat gets shorter (44 mm instead of 57), still two keyed supports. Pair it with the
80 mm Z spacer (a5_z_spacer.py, default --ext 80): the tip drops 20 mm, so Z must home
20 mm higher or G28 Z drives the tip into the bed cover.

v5 (2026-11-18): mirrored in X (v4 was the wrong hand; --no-mirror restores it), and the
snap caps are now locked in Z. In v4 the barbs and body recesses ran the full block
height, so a cap could slide up or down off the body. Now each barb is shorter than its
arm (BARB_ZM) and drops into a closed window in the body whose roof and floor hold it
(Z_CLR clearance).

v4 (2026-11-17). REPLACES the black sheet-metal fan bracket (remove it and the fan; we
never heat). Same L shape as that bracket: a side leg on the M2 face of the head (two M2
screws, one above the other, 12 mm apart), a 90 degree turn around the front corner, and
a front plate across the front of the head. The probe hangs in front of the front plate,
centered on the head, so nothing sticks out past either side face.

THE PROBE LOADS FROM THE FRONT. The H20 and H10 tips are rings much wider than the blade,
so nothing may have to pass over the tip. Both holding features are split on the probe
axis into a body half and a SNAP-ON front cap (no screws):
    lower guide: a "+" slot that keys the blade (both orientations)
    seat:        a "+" slot in a flat floor; the flat flange at the bottom of the rubber
                 handle sits on it inside a collar (sets the tip height)
Each cap has two flexible side arms with barbs that click over a step on the body. Pull
the caps straight off, lay the probe into the body halves, push the caps back on.
To change field orientation: caps off, turn the probe 90 degrees, caps on.
    Blade parallel to the front plate (along X): loop faces Y, reads Hy.
    Blade perpendicular to the front plate (along Y): loop faces X, reads Hx.
The lower guide sits above the widest tip (--tip-h) and the probe axis sits far enough
in front of the plate (--tip-w) that the H20 ring clears everything in both orientations.

Optional lip: with --flange-t (and --handle-d, the handle just above the flange) the
collar gets an inward lip over the flange, so a tugging cable cannot lift the probe.

Outputs (cad/out/), all printed upright as exported (arms flex across the layers' length,
which is the strong direction). PETG, 4 walls:
    a5_probe_mount.stl, a5_probe_cap_guide.stl, a5_probe_cap_seat.stl

Local axes: x = 0 is the M2 face of the head, +x runs across the head toward the far
side; y = 0 is the M2 screw axis, +y toward the front; z = 0 is the old nozzle tip.

Screws to the head: holes about 5 mm deep, M2 x 4 on hand. Each head sits in a pocket with
FLOOR mm of plastic under it (default 1.6 mm). M2 x 6 with --floor 3 --side-t 5 is stiffer.

HEAD SIZES: Jacob's ruler photos (2026-11-15, +/-1.5 mm) and calipers: head front about
32 mm wide from the M2 face (--front-w 33.5 incl. the 1.5 mm sheet), old bracket bottom
9.5 mm below the lower screw, fan 13 mm thick (the front plate sits where it was).
PROBE (calipers, Jacob, 2026-11-17): blade 9.25 x 2.0 mm; flange 16.5 mm, flat, at the
bottom of the handle; flange underside to tip 84.75 mm on all four probes; H20 ring
23.8 mm wide, 24 mm tall.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import manifold3d as m3

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from emcscan.cad import box, cyl, save  # noqa: E402

SCREW_Z = (30.0, 42.0)        # lower and upper M2, above the old nozzle tip (Jacob)
HOLE_D, POCKET_D = 2.4, 4.8   # M2 clearance, M2 head clearance (head about 3.8 mm)
GUIDE_T = 8.0                 # lower guide block height
SEAT_T = 5.0                  # seat floor under the flange
WALL = 3.0                    # collar and guide walls
LIP_T = 1.5                   # optional lip over the flange
# snap arms
ARM_T = 1.8                   # arm thickness (flexes in X)
ARM_GAP = 0.3                 # arm to body side
BARB = 0.8                    # barb engagement (= arm deflection when snapping on)
STEP = 1.0                    # body side recess behind the catch (BARB + 0.2 clear)
ARM_L = 10.0                  # split plane to the catch
BARB_L = 2.5                  # barb ramp length, behind the catch
BARB_ZM = 2.0                 # barb stops this far short of the arm's top and bottom
Z_CLR = 0.2                   # barb to window clearance in Z (the Z lock)


def xcyl(y: float, z: float, x0: float, h: float, d: float, seg: int = 32) -> m3.Manifold:
    """Cylinder along +X from x0."""
    return m3.Manifold.cylinder(h, d / 2, d / 2, seg).rotate((0, 90, 0)).translate((x0, y, z))


def cross_slot(ax: float, ay: float, z0: float, h: float, w: float, t: float) -> m3.Manifold:
    a = box(ax - w / 2, ay - t / 2, z0, ax + w / 2, ay + t / 2, z0 + h)
    b = box(ax - t / 2, ay - w / 2, z0, ax + t / 2, ay + w / 2, z0 + h)
    return a + b


def snap(px: float, ay: float, yfo: float, hw: float, reach: float, z0: float, z1: float):
    """Arms + barbs for a cap of half width hw, and the matching body recesses."""
    arms, cuts = None, None
    yc = ay - ARM_L                                           # catch line
    for s in (-1, 1):
        xi = px + s * (hw + ARM_GAP)                          # arm inner face
        xo = px + s * (hw + ARM_GAP + ARM_T)
        arm = box(min(xi, xo), yc - BARB_L, z0, max(xi, xo), ay + reach, z1)
        bridge = box(min(px + s * hw, xo), ay, z0, max(px + s * hw, xo), ay + reach, z1)
        xt = px + s * (hw - BARB)                             # barb tip
        pts = [(x, y, z) for z in (z0, z1)
               for (x, y) in ((xi, yc), (xi, yc - BARB_L), (xt, yc - 0.6), (xi + s * 0.01, yc - 0.6))]
        barb = m3.Manifold.hull_points(pts) ^ box(px - 50, yc - 50, z0 + BARB_ZM, px + 50, yc + 50, z1 - BARB_ZM)
        a = arm + bridge + barb
        arms = a if arms is None else arms + a
        # closed window, not a full-height notch: its roof and floor trap the barb in Z
        cut = box(min(px + s * (hw - STEP), px + s * (hw + 5)), yfo, z0 + BARB_ZM - Z_CLR,
                  max(px + s * (hw - STEP), px + s * (hw + 5)), yc, z1 - BARB_ZM + Z_CLR)
        cuts = cut if cuts is None else cuts + cut
    return arms, cuts


def build(a):
    """Returns (body, cap_guide, cap_seat, info) in mount coordinates."""
    sw, st = a.blade_w + a.fit, a.blade_t + a.fit           # slot arm length and width
    rf = a.flange_d / 2 + a.fit                               # flange pocket radius
    yf = a.screw_to_front - a.sheet - a.fan_t + a.gap         # heatsink front face plus a gap
    yfo = yf + a.front_t                                      # outer face of our front plate
    x_far = a.front_w - a.sheet                               # far edge, from the M2 face
    px = a.probe_x if a.probe_x is not None else x_far / 2    # probe axis across the head
    sr = rf + WALL                                            # seat and collar half width
    gw = sw / 2 + WALL + STEP                                 # guide half width
    ay = yfo + max(sr - 1, a.tip_w / 2 + a.tip_gap, ARM_L + BARB_L + 3)
    z_m2 = SCREW_Z[0] - a.below                               # bottom edge of the side leg
    gz = a.tip_z + a.tip_h + a.tip_gap                        # lower guide bottom, above the ring
    if a.guide_floor:                                         # v6: never below the side leg
        gz = max(gz, z_m2)
    fz = a.tip_z + a.blade_len                                # seat top = flange underside
    lip = a.flange_t is not None
    collar_h = (a.flange_t + 0.3 + LIP_T) if lip else a.collar_h
    top = fz + collar_h
    outer = max(sr, gw) + ARM_GAP + ARM_T
    if px - outer < -a.side_t or px + outer > x_far:
        raise SystemExit("seat or guide (with snap arms) wider than the head; move --probe-x")
    if fz - SEAT_T < gz + GUIDE_T + 10:
        raise SystemExit("seat too close to the lower guide; check --blade-len and --tip-h")

    z0 = min(z_m2, gz)
    side = box(-a.side_t, -a.screw_to_rear, z_m2, 0, yfo, SCREW_Z[1] + 12)
    front = box(-a.side_t, yf, z0, x_far, yfo, top)
    g_reach, s_reach = sw / 2 + WALL, sr
    lower = box(px - gw, yfo - 1, gz, px + gw, ay + g_reach, gz + GUIDE_T)
    seat = box(px - sr, yfo - 1, fz - SEAT_T, px + sr, ay + s_reach, top)

    span = ay - (yfo - 1)
    gus = None
    for xc in (px - (sr - STEP - 1.5), px + (sr - STEP - 1.5)):
        pts = [(x, y, z) for x in (xc - 1.5, xc + 1.5)
               for (y, z) in ((yfo - 1, fz - SEAT_T), (ay - ARM_L - BARB_L - 0.5, fz - SEAT_T),
                              (yfo - 1, fz - SEAT_T - span))]
        g = m3.Manifold.hull_points(pts)
        gus = g if gus is None else gus + g

    part = side + front + lower + seat + gus
    part = part - cross_slot(px, ay, gz - 1, GUIDE_T + 2, sw, st)
    part = part - cross_slot(px, ay, fz - SEAT_T - 1, SEAT_T + 2, sw, st)
    if lip:
        part = part - cyl(px, ay, fz, a.flange_t + 0.3, 2 * rf, 96)
        part = part - cyl(px, ay, fz + a.flange_t + 0.29, LIP_T + 1, a.handle_d + 2 * a.fit, 96)
    else:
        part = part - cyl(px, ay, fz, collar_h + 1, 2 * rf, 96)

    for z in SCREW_Z:                                         # M2 to the head
        part = part - xcyl(0, z, -a.side_t - 1, a.side_t + 2, HOLE_D)
        part = part - xcyl(0, z, -a.side_t - 1, a.side_t + 1 - a.floor, POCKET_D)

    split = box(-50, ay, -50, 100, 200, 300)
    body = part - split
    caps = part ^ split
    for hw, reach, za, zb in ((gw, g_reach, gz, gz + GUIDE_T), (sr, s_reach, fz - SEAT_T, top)):
        arms, cuts = snap(px, ay, yfo, hw, reach, za, zb)
        caps = caps + arms
        body = body - cuts
    pieces = sorted(caps.decompose(), key=lambda m: m.bounding_box()[2])
    if len(pieces) != 2:
        raise SystemExit(f"expected 2 caps, got {len(pieces)}")
    if (body ^ caps).volume() > 1e-3:
        raise SystemExit("caps overlap the body")
    info = dict(ay=ay, yfo=yfo, gz=gz, fz=fz, top=top, z0=z0, px=px)
    return body, pieces[0], pieces[1], info


def to_bed(m: m3.Manifold) -> m3.Manifold:
    lo = m.bounding_box()
    return m.translate((-lo[0], -lo[1], -lo[2]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_argument_group("probe (calipers, Jacob, 2026-11-17)")
    g.add_argument("--blade-w", type=float, default=9.25, help="blade width, mm")
    g.add_argument("--blade-t", type=float, default=2.0, help="blade thickness incl. coating")
    g.add_argument("--blade-len", type=float, default=84.75, help="flange underside to probe tip (all four)")
    g.add_argument("--flange-d", type=float, default=16.5, help="flat flange at the bottom of the handle")
    g.add_argument("--flange-t", type=float, default=None, help="flange thickness; adds a lip over it (needs --handle-d)")
    g.add_argument("--handle-d", type=float, default=None, help="handle diameter just above the flange (lip hole)")
    g.add_argument("--tip-w", type=float, default=23.8, help="widest tip, outside (H20 ring)")
    g.add_argument("--tip-h", type=float, default=24.0, help="widest tip, from the tip end up to the blade")
    g.add_argument("--tip-gap", type=float, default=4.0, help="clearance around the widest tip")
    g.add_argument("--fit", type=float, default=0.4, help="clearance on slots and flange pocket")
    h = ap.add_argument_group("head (photos and calipers)")
    h.add_argument("--screw-to-front", type=float, default=19.6, help="M2 axis to outer front face of old bracket")
    h.add_argument("--screw-to-rear", type=float, default=13.0, help="M2 axis to rear edge of old bracket leg")
    h.add_argument("--front-w", type=float, default=33.5, help="old bracket front plate width incl. side leg")
    h.add_argument("--below", type=float, default=9.5, help="lower M2 to old bracket bottom edge")
    h.add_argument("--sheet", type=float, default=1.5, help="old bracket sheet thickness")
    h.add_argument("--fan-t", type=float, default=13.0, help="fan thickness, removed (Jacob, calipers)")
    h.add_argument("--gap", type=float, default=0.5, help="air gap between front plate and heatsink")
    p = ap.add_argument_group("this part")
    p.add_argument("--side-t", type=float, default=3.5, help="side leg thickness (sticks out past the M2 face)")
    p.add_argument("--front-t", type=float, default=4.0)
    p.add_argument("--floor", type=float, default=1.6, help="plastic under each M2 head (M2x4: 1.6, M2x6: 3)")
    p.add_argument("--probe-x", type=float, default=None, help="probe axis from the M2 face (default: centered)")
    p.add_argument("--tip-z", type=float, default=-20.0,
                   help="probe tip height relative to the old nozzle tip (v6: -20; v5 was 0)")
    p.add_argument("--guide-floor", action=argparse.BooleanOptionalAction, default=True,
                   help="keep the lower guide no lower than the side leg (v6); --no-guide-floor for v5 placement")
    p.add_argument("--collar-h", type=float, default=3.0, help="collar around the flange (no lip)")
    p.add_argument("--mirror", action=argparse.BooleanOptionalAction, default=True,
                   help="mirror in X (default, v5: the hand that fits the head); --no-mirror for the v4 hand")
    args = ap.parse_args()
    if (args.flange_t is None) != (args.handle_d is None):
        raise SystemExit("--flange-t and --handle-d go together")
    body, cap_g, cap_s, info = build(args)
    if args.mirror:
        body, cap_g, cap_s = (m.mirror((1, 0, 0)) for m in (body, cap_g, cap_s))
    out = ROOT / "cad" / "out"
    for name, m in (("a5_probe_mount.stl", body), ("a5_probe_cap_guide.stl", cap_g),
                    ("a5_probe_cap_seat.stl", cap_s)):
        save(to_bed(m), out / name)
    bb = body.bounding_box()
    t = args.tip_z                                            # heights below are from the TIP
    print(f"body {bb[3]-bb[0]:.1f} x {bb[4]-bb[1]:.1f} x {bb[5]-bb[2]:.1f} mm; probe axis "
          f"{info['ay']-info['yfo']:.1f} mm in front of the plate; lower guide {info['gz'] - t:.1f} mm "
          f"above the tip; flange seat {info['fz'] - t:.2f} mm above the tip")
    print(f"LOWEST point of the mount: {bb[2] - t:.1f} mm above the tip (set probe.head_h to this, "
          f"rounded down); tip {-t:.1f} mm below the old nozzle tip")
