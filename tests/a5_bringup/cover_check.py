import sys, itertools, trimesh, numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
out = sys.argv[1]; s = float(sys.argv[2]) / 2; k = 17.0
names = ["0_front_left", "1_front_right", "2_back_right", "3_back_left"]
offs = [(0, 0), (s, 0), (s - k, s), (0, s - k)]
ms = []
for n, o in zip(names, offs):
    m = trimesh.load(f"{out}/a5_cover_{n}.stl"); m.apply_translation((o[0], o[1], 0)); ms.append(m)
for (i, a), (j, b) in itertools.combinations(enumerate(ms), 2):
    v = a.intersection(b).volume if a.bounds[0][0] < b.bounds[1][0] else 0
    print(f"overlap {i}-{j}: {v:.3f} mm3")
tot = sum(m.volume for m in ms); print(f"total volume {tot/1000:.0f} cm3, outer bounds", np.round(trimesh.util.concatenate(ms).bounds, 2).tolist())
fig, ax = plt.subplots(figsize=(7, 7)); cols = ["#4C78A8", "#F58518", "#54A24B", "#B279A2"]
for m, c, n in zip(ms, cols, names):
    sec = m.section(plane_origin=(0, 0, 6), plane_normal=(0, 0, 1)); p2, _ = sec.to_planar(to_2D=np.eye(4))
    for poly in p2.polygons_full:
        x, y = poly.exterior.xy; ax.fill(x, y, color=c, alpha=0.75, lw=0.5, ec="k")
    sec = m.section(plane_origin=(0, 0, 11.8), plane_normal=(0, 0, 1)); p2, _ = sec.to_planar(to_2D=np.eye(4))
    for poly in p2.polygons_full:
        for h in poly.interiors: x, y = h.xy; ax.plot(x, y, "k", lw=0.6)
    cx, cy = m.centroid[:2]; ax.text(cx, cy, n.replace("_", " "), ha="center", fontsize=10)
ax.set_aspect("equal"); ax.set_title("A5 bed cover, top view (front at bottom)"); ax.set_xlabel("X mm"); ax.set_ylabel("Y mm")
fig.savefig("C:/work/ve_emc_pipeline/tests/a5_bringup/cover_top.png", dpi=110, bbox_inches="tight")
