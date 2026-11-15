"""Talk to the JG Aurora A5 (Marlin 1.1.8, 250000 baud). Usage: aurora.py COM15 BAUD "G28" "M114" ...
Each command waits for 'ok' (long timeout for G28/M400). Prints everything received."""
import sys, time, serial

def show(b):
    s = b.decode("latin-1").rstrip()
    print("<", s.encode("ascii", "replace").decode(), flush=True)
    return s

port, baud = sys.argv[1], int(sys.argv[2])
cmds = sys.argv[3:]
sp = serial.Serial(port, baud, timeout=0.1)
t_end = time.time() + 4.0               # board resets when the port opens; skip boot text
while time.time() < t_end:
    sp.readline()
for c in cmds:
    wait = 120.0 if c.startswith(("G28", "M400")) else 8.0
    print(">", c, flush=True)
    t0 = time.time()
    sp.write((c + "\n").encode())
    t_end = t0 + wait
    while time.time() < t_end:
        line = sp.readline()
        if line and show(line).startswith("ok"):
            print(f"   ({time.time() - t0:.1f} s)", flush=True)
            break
    else:
        print("!! no ok within", wait, "s", flush=True)
sp.close()
