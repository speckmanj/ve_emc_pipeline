"""Temporary live check of the 1afe2a7 TinySA fixes. Safe to delete."""
import sys, time
sys.path.insert(0, r"C:\work\ve_emc_pipeline")
import numpy as np
from emcscan import config as cfgmod
from emcscan.analyzers import make_analyzer

cfg = cfgmod.load(r"C:\work\ve_emc_pipeline\configs\a1mini_tinysa.toml")
an = make_analyzer(cfg)
an.connect()
print("version attr:", repr(an.version))
print("hw attr     :", repr(an.hw))

bands = cfg["analyzer"]["bands"]
ok = fail = 0
t0 = time.time()
for i in range(20):
    for lna in (False, True):
        an.set_lna(lna)
        an.cmd("rbw 100" if i % 2 else "rbw auto")      # setting change right before a sweep (the trigger seen 2026-11-12)
        for b in bands[:2]:
            try:
                f, lv = an.sweep(b)
                ok += 1
            except RuntimeError as e:
                fail += 1
                print("FAIL:", e)
print(f"sweeps ok {ok}, failed {fail}, retries {an.retries}, {time.time() - t0:.0f} s")

print("--- straddle warning check ---")
an.cmd("rbw auto")
f, lv = an.sweep({"name": "straddle 700-900", "start_hz": 700e6, "stop_hz": 900e6, "points": 200})
lo, hi = np.median(lv[f < 800e6]), np.median(lv[f >= 800e6])
print(f"LNA {an.lna_on}: median below 800 MHz {lo:.1f} dBm, above {hi:.1f} dBm, step {lo - hi:.1f} dB")
an.set_lna(False)
f, lv = an.sweep({"name": "straddle 700-900", "start_hz": 700e6, "stop_hz": 900e6, "points": 200})
lo, hi = np.median(lv[f < 800e6]), np.median(lv[f >= 800e6])
print(f"LNA off: median below 800 MHz {lo:.1f} dBm, above {hi:.1f} dBm, step {lo - hi:.1f} dB")

an.set_lna(True)
an.cmd("rbw 100")
print("--- close() with LNA on, RBW 100 ---")
an.close()

import serial
time.sleep(0.5)
s = serial.Serial(an.port, 115200, timeout=0.5)
def q(c):
    s.write(c.encode() + b"\r"); time.sleep(0.6)
    return s.read(4096).decode(errors="replace").replace("\r", "").strip()
s.write(b"\r"); time.sleep(0.3); s.read(4096)
for c in ("rbw", "lna", "status"):
    print(f"after close, '{c}':", repr(q(c)))
s.close()
