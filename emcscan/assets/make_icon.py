r"""Draw the EMC Scanner icon (emc_scanner.ico + emc_scanner.png). Run once after a change:

    .venv\Scripts\python emcscan\assets\make_icon.py
    .venv\Scripts\python emcscan\assets\make_icon.py --brand <VE_Logos-06.png>   (re-cut the V)

A near-field heat map on the Valence navy tile: the Valence "V" is the hot spot (white
hot), cooling through red, orange, yellow, green and cyan to blue toward the edges;
the rings follow the V outline (a badge; raise BLEND for rounder rings).
The V shape lives in v_mark.png, cut from the brand file VE_Logos-06.png (the two largest
pieces of the white signet are the V strokes).
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

HERE = Path(__file__).resolve().parent
S = 2048                                    # draw big, scale down for each icon size
NAVY = (12, 0, 90)                          # Valence brand navy (VE_Logos-07 background)
CORE = (255, 255, 255)
# hottest first; band i reaches FIRST + i * STEP (fractions of S) from the V
BANDS = [(232, 36, 36), (250, 128, 30), (250, 208, 40), (80, 196, 90),
         (30, 168, 210), (40, 90, 200), (32, 48, 150)]
FIRST, STEP = 0.025, 0.040
BLEND = 0.0        # 0 = rings follow the V outline (badge), 1 = round rings, 0.5 = V core, round edge


def cut_v(brand: Path):
    a = np.array(Image.open(brand).convert("RGBA"))[:, :, 3] > 128
    lab, n = ndimage.label(a)
    sizes = ndimage.sum(a, lab, range(1, n + 1))
    keep = np.argsort(sizes)[::-1][:2] + 1              # the two V strokes
    v = np.isin(lab, keep)
    ys, xs = np.nonzero(v)
    v = v[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    Image.fromarray((v * 255).astype(np.uint8)).save(HERE / "v_mark.png")
    print("wrote", HERE / "v_mark.png")


def draw() -> Image.Image:
    v = Image.open(HERE / "v_mark.png").convert("L")
    h = int(S * 0.36)
    w = round(v.width * h / v.height)
    v = v.resize((w, h), Image.LANCZOS)
    vm = np.zeros((S, S), bool)
    x0, y0 = (S - w) // 2, int(S * 0.31)
    vm[y0:y0 + h, x0:x0 + w] = np.array(v) > 127
    dv = ndimage.distance_transform_edt(~vm) / S          # distance to the V
    yy, xx = np.mgrid[0:S, 0:S] / S
    r = np.hypot(yy - (y0 + h * 0.45) / S, xx - 0.5)      # distance to the V's middle
    f = (1 - BLEND) * dv + BLEND * np.maximum(r - 0.14, 0)
    rgb = np.zeros((S, S, 3), np.uint8)
    rgb[:] = NAVY
    for i in reversed(range(len(BANDS))):                  # coolest first, hotter on top
        rgb[f <= FIRST + STEP * i] = BANDS[i]
    rgb[vm] = CORE
    img = Image.fromarray(rgb).convert("RGBA")
    m = 64 * S // 1024
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], radius=190 * S // 1024, fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out.resize((1024, 1024), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", type=Path, help="re-cut v_mark.png from this brand PNG first")
    a = ap.parse_args()
    if a.brand:
        cut_v(a.brand)
    img = draw()
    img.resize((256, 256), Image.LANCZOS).save(HERE / "emc_scanner.png")
    img.save(HERE / "emc_scanner.ico",
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", HERE / "emc_scanner.ico")


if __name__ == "__main__":
    main()
