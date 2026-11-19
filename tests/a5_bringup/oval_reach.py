"""Sweep two ovals in two cover holes D apart; report which board-hole spacings are reachable
without the oval bodies colliding. Usage: python oval_reach.py [tip_r]"""
import math, sys
import manifold3d as m3

END_R, CLEAR = 9.0, 0.5
TIP_R = float(sys.argv[1]) if len(sys.argv) > 1 else 9.0

def outline(r, ang, px):
    cs = (m3.CrossSection.circle(END_R + CLEAR / 2, 48)
          + m3.CrossSection.circle(TIP_R + CLEAR / 2, 48).translate((r, 0))).hull()
    return cs.rotate(ang).translate((px, 0))

def reach(r1, r2, D, step=3):
    ok = set()
    for a1 in range(0, 360, step):
        o1 = outline(r1, a1, 0)
        t1 = (r1 * math.cos(math.radians(a1)), r1 * math.sin(math.radians(a1)))
        for a2 in range(0, 360, step):
            t2 = (D + r2 * math.cos(math.radians(a2)), r2 * math.sin(math.radians(a2)))
            s = math.dist(t1, t2)
            if round(s) in ok:
                continue
            if (o1 ^ outline(r2, a2, D)).area() < 1e-6:
                ok.add(round(s))
    return ok

pairs = {"neighbor 40": 40.0, "diagonal 56.6": 40 * math.sqrt(2), "far 80": 80.0}
R = (10, 15, 20)
total = set()
for name, D in pairs.items():
    for i, r1 in enumerate(R):
        for r2 in R[i:]:
            ok = reach(r1, r2, D)
            total |= ok
            print(f"{name:14s} r{r1}+r{r2}: {min(ok):3d} to {max(ok):3d} mm")
gaps = [s for s in range(0, 121) if s not in total]
print(f"TIP_R {TIP_R}: min spacing {min(total)} mm; unreachable 0-120: {gaps}")
