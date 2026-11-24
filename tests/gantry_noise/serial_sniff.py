"""Does the JG Aurora A5 touchscreen share the USB serial line? (2026-11-24)

Opens the printer port, sends nothing, and prints every line that arrives for --secs
seconds, with a timestamp. Press touchscreen buttons while it listens. If lines appear
(ok, T:..., echo:, X:... Y:...), the screen's traffic reaches the USB port, and the
emcscan scripts will read the screen's replies as their own.

Usage (repo root): python tests/gantry_noise/serial_sniff.py <job.toml> [--secs 60]
Note: opening the port resets the A5 (homing is lost), as with every emcscan command.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from emcscan import config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--secs", type=float, default=60.0)
    a = ap.parse_args()
    import serial
    pc = config.load(a.job)["printer"]
    ser = serial.Serial(pc["port"], int(pc["baud"]), timeout=0.2)
    print(f"Opened {pc['port']} at {pc['baud']}. Waiting 4 s for the A5 to finish booting...")
    time.sleep(4.0)
    boot = ser.read(ser.in_waiting or 1)
    print(f"(boot text: {len(boot)} bytes, ignored)")
    ser.reset_input_buffer()
    print(f"Listening {a.secs:.0f} s, sending nothing. Spend 10 s NOT touching the screen,")
    print("then press screen buttons (Move/jog Z, Home, the temperature page).\n")
    t0, n = time.time(), 0
    while time.time() - t0 < a.secs:
        line = ser.readline().decode(errors="replace").strip()
        if line:
            n += 1
            print(f"  {time.time() - t0:5.1f} s  {line[:120]}")
    ser.close()
    print(f"\n{n} lines received.")
    print("0 lines while pressing buttons = the screen has its own line; the crash is something else."
          if n == 0 else
          "Lines arrived = the screen's traffic reaches the USB port. Do not touch the screen while a script is connected.")


if __name__ == "__main__":
    main()
