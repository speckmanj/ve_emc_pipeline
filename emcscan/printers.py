"""Printer drivers. Each one only has to move the nozzle and report when it is done.

All of them share the same small interface (class Printer):
    connect()  prepare()  home()  move_to(x, y, z)  lift()  close()

    SimPrinter          no hardware, for demos and tests
    BambuMqttPrinter    Bambu A1 mini (or other Bambu) in LAN Only + Developer Mode
    SerialGcodePrinter  any Marlin/Prusa printer on USB (Ender, Prusa, ...)
    MoonrakerPrinter    Klipper printers (Sovol SV08, Voron, ...)

How we know a move is finished:
    Serial and Moonraker printers answer "ok" only after M400 (wait for moves to end),
    so we know exactly. Bambu's MQTT channel does not report move completion, so we
    wait for the computed travel time plus a margin (see _wait_for_motion).
"""
from __future__ import annotations

import json
import math
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request

from .plan import move_time


class ScreenActivity(RuntimeError):
    """The printer's own touchscreen was used while we were connected (shared serial line)."""


class Printer:
    # True when send(wait=True) itself returns only after the motion is done (serial with
    # M400, Moonraker, the simulator). False (Bambu): we have to sleep the estimated time.
    blocking = False

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.pc = cfg["printer"]
        self.pos: tuple[float, float, float] | None = None   # last commanded nozzle position

    # --- to implement per printer ---
    def connect(self): ...
    def send(self, gcode: str, wait: bool = False): raise NotImplementedError
    def close(self): ...

    # --- shared behaviour ---
    def prepare(self):
        """Heaters off, absolute coordinates, gentle acceleration."""
        a = int(self.pc["accel"])
        self.send("M104 S0\nM140 S0\nG90\nM204 S{0}".format(a), wait=True)

    def home(self):
        self.send("G28", wait=True)
        self.pos = None
        self._sleep_worst_case(extra=25.0)   # homing time is not reported; be generous

    def move_to(self, x: float, y: float, z: float, via_z: float | None = None):
        """Move the nozzle (or probe tip). Goes up first, then across, then down, so it
        never drags diagonally into the board. via_z: cross at least this high (the
        highest legal height along the way, from the height map)."""
        x, y, z = round(x, 3), round(y, 3), round(z, 3)
        fxy, fz = self.pc["feed_xy"], self.pc["feed_z"]
        cur = self.pos
        travel = z if cur is None else max(z, cur[2])
        if via_z is not None:
            travel = max(travel, round(float(via_z), 3))
        if cur is None:
            # Where Z really is is unknown: never cross lower than safe_z, or the first
            # "G1 Z" could be a move DOWN into the board followed by a sideways drag.
            travel = max(travel, float(self.pc["safe_z"]))
        # Absolute mode on every move: a G91 left behind (an error between G91 and G90)
        # would turn every coordinate below into a relative jump.
        self.send("G90")
        if cur is None:
            self._line(f"G1 Z{travel} F{fz:.0f}", None, None, fz)
            self._line(f"G1 X{x} Y{y} F{fxy:.0f}", None, None, fxy)
        else:
            if travel > cur[2]:
                self._line(f"G1 Z{travel} F{fz:.0f}", cur, (cur[0], cur[1], travel), fz)
            self._line(f"G1 X{x} Y{y} F{fxy:.0f}", (cur[0], cur[1], travel), (x, y, travel), fxy)
        if z < travel:
            self._line(f"G1 Z{z} F{fz:.0f}", (x, y, travel), (x, y, z), fz)
        self.pos = (x, y, z)
        time.sleep(self.pc["settle_s"])

    def jog(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0):
        """Relative move from where the head REALLY is (asked fresh where the printer can
        report it), Z first when going up. A jog from a stale position is an absolute move
        to somewhere else."""
        cur = self.where(strict=True)
        if cur is None:
            raise RuntimeError("position unknown: home or go to a known point first")
        x, y, z = cur
        self.move_to(x + dx, y + dy, z + dz)

    def where(self, strict: bool = False):
        """Position as the printer reports it, or the last commanded one (printers that
        cannot report it)."""
        return self.pos

    def quiet(self):
        """Switch every stepper driver off for a sweep (M84), then wait quiet_settle_s.
        2026-11-24 gantry noise test (tests/gantry_noise): the A5's four drivers holding
        still add up to +28 dB at 30-40 MHz and +4 to +10 dB up to 300 MHz; any single one
        off barely helps, all off brings it within about 2 dB of the A5 switched off at
        the wall. The next move switches them back on (wake, or any G1)."""
        self.send("M84", wait=True)
        time.sleep(float(self.pc.get("quiet_settle_s", 0.1)))

    def wake(self):
        """Drivers back on (M17) before the next move."""
        self.send("M17", wait=True)

    def lift(self):
        """Raise to safe_z (used at the end and on Ctrl+C). Z only: the command carries no
        X or Y, so a stale remembered position can never send the head somewhere else."""
        sz = float(self.pc["safe_z"])
        fz = self.pc["feed_z"]
        if self.pos is None:
            self.send(f"G91\nG1 Z10 F{fz:.0f}\nG90", wait=True)
            if not self.blocking:
                self._sleep_worst_case()
            return
        x, y, z = self.pos
        if z < sz:
            self._line(f"G90\nG1 Z{sz:.3f} F{fz:.0f}", (x, y, z), (x, y, sz), fz)
            self.pos = (x, y, sz)

    def park(self):
        """On close: clear the board in Z (straight up, never sideways), then home X and Y
        only. Z stays where it is: homing Z would drive the probe down to the switch."""
        self.lift()
        self.send("G28 X Y", wait=True)
        self.pos = None
        if not self.blocking:                   # serial/Moonraker already waited (M400)
            self._sleep_worst_case(extra=10.0)

    def _line(self, gcode, start, end, feed):
        self.send(gcode, wait=True)
        self._wait_for_motion(start, end, feed)

    def _wait_for_motion(self, start, end, feed):
        """Default for printers that already blocked in send(wait=True): nothing to do."""

    def _sleep_worst_case(self, extra: float = 0.0):
        diag = math.hypot(self.pc["bed_x"], self.pc["bed_y"])
        time.sleep(move_time(diag, self.pc["feed_xy"], self.pc["accel"]) * 1.3 + 1.0 + extra)


class SimPrinter(Printer):
    """Pretends to move. time_scale 0 = instant, 1 = real time."""
    blocking = True

    def connect(self):
        print("[printer] simulator")

    def send(self, gcode, wait=False):
        pass

    def home(self):
        self.pos = (0.0, 0.0, 0.0)

    def _wait_for_motion(self, start, end, feed):
        k = float(self.cfg["sim"]["time_scale"])
        if k > 0 and start and end:
            time.sleep(move_time(math.dist(start, end), feed, self.pc["accel"]) * k)

    def _sleep_worst_case(self, extra=0.0):
        pass

    def move_to(self, x, y, z, via_z=None):
        settle = self.pc["settle_s"]
        self.pc["settle_s"] = settle * float(self.cfg["sim"]["time_scale"])
        try:
            super().move_to(x, y, z, via_z)
        finally:
            self.pc["settle_s"] = settle


class SerialGcodePrinter(Printer):
    """Marlin-style printer on a COM port. Every line waits for 'ok'."""
    blocking = True
    POS_TOL = 0.05                              # mm; M114 prints 2 decimals

    def move_to(self, x, y, z, via_z=None):
        """Ask the printer where it really is, move (Z up first, from the REAL height), then
        check where it ended up.

        Before (2026-11-22, snapped the mock H20): the climb was decided from the position
        the app remembered. After the head was moved some other way (the printer's own
        screen, or anything the app did not see), "Z up to the travel height" was computed
        from a stale Z, could be no move at all or a move DOWN, and the X/Y move then dragged
        the probe sideways through the board. Now every move starts from M114.
        After the move: Marlin clamps a move past its soft limits WITHOUT an error, so the
        position (and every reading logged at it) would silently be wrong. Refuse instead."""
        self.pos = self.where(strict=True)
        super().move_to(x, y, z, via_z)
        want = self.pos
        got = self.where()
        if got is not None and want is not None and got is not want:
            off = max(abs(g - w) for g, w in zip(got, want))
            if off > self.POS_TOL:
                raise RuntimeError(
                    f"printer stopped at X{got[0]:.2f} Y{got[1]:.2f} Z{got[2]:.2f}, not at "
                    f"X{want[0]:.2f} Y{want[1]:.2f} Z{want[2]:.2f} ({off:.2f} mm off): a soft "
                    f"limit clamped the move. Check the calibration and the scan area.")

    def connect(self):
        import serial
        self.ser = serial.Serial(self.pc["port"], int(self.pc["baud"]), timeout=1)
        time.sleep(2.0)                         # many boards reset when the port opens
        self.ser.reset_input_buffer()
        # Handshake: a wrong baud gives garbage and no 'ok', which used to look like a
        # 120 s hang per line. Ask for the firmware name and fail fast with a clear message.
        self.ser.write(b"\nM115\n")
        reply, deadline = b"", time.time() + 4.0
        while time.time() < deadline and b"ok" not in reply:
            reply += self.ser.read(256)
        if b"FIRMWARE_NAME" not in reply and b"ok" not in reply:
            self.ser.close()
            raise ConnectionError(
                f"no G-code reply on {self.pc['port']} at {self.pc['baud']} baud "
                f"(got {reply[:40]!r}). Check [printer] baud (JG Aurora A5 = 250000) and port "
                f"(Device Manager > Ports, CH340), and close any other program using the port.")
        name = re.search(rb"MACHINE_TYPE:([^\r\n]*?)(?: EXTRUDER| UUID|\r|\n|$)", reply)
        time.sleep(0.5)                         # let any late boot text arrive, then drop it,
        self.ser.reset_input_buffer()           # so the touchscreen guard starts from silence
        print(f"[printer] serial G-code on {self.pc['port']}"
              + (f": {name.group(1).decode(errors='replace').strip()}" if name else ""))

    def _check_quiet_line(self):
        """2026-11-24 (serial_sniff test): the A5 touchscreen shares the USB serial line.
        Its commands and Marlin's replies to them arrive on our port, so after any screen
        use our 'ok's and M114 reads belong to someone else. Between our commands the line
        must be silent; anything waiting there (other than a busy keepalive) means the
        screen was used, and nothing more is sent."""
        if not self.ser.in_waiting:
            return
        junk = self.ser.read(self.ser.in_waiting).decode(errors="replace")
        lines = [ln.strip() for ln in junk.splitlines() if ln.strip()]
        lines = [ln for ln in lines if not ln.startswith("echo:busy")
                 and "SD init" not in ln and "SD card" not in ln]     # late boot text
        if lines:
            raise ScreenActivity(
                "the printer's touchscreen was used while connected (it shares the USB line, "
                f"so replies are no longer ours; saw {lines[:3]!r}). Nothing more was sent. "
                "Leave the screen alone, reconnect, and HOME again before moving.")

    def send(self, gcode, wait=False):
        lines = [g for g in gcode.splitlines() if g.strip()]
        if wait:
            lines.append("M400")                # returns 'ok' only when motion is done
        self._check_quiet_line()
        for line in lines:
            self.ser.write((line + "\n").encode())
            deadline = time.time() + 120
            while time.time() < deadline:
                resp = self.ser.readline().decode(errors="replace").strip()
                if resp.startswith("ok"):
                    break
                if resp.lower().startswith("error"):
                    raise RuntimeError(f"printer error on '{line}': {resp}")
            else:
                raise TimeoutError(f"no 'ok' for '{line}'")

    def prepare(self):
        """Also keep the motors powered while the app is connected (M84 S0 = no idle
        timeout). Marlin switches them off after about 2 minutes idle; the head can then be
        pushed or the Z screw turned by hand, and neither Marlin nor the app would know:
        M114 keeps reporting the old position, and every move after is planned from it."""
        super().prepare()
        self.send("M84 S0", wait=True)

    def park(self):
        super().park()
        self.send("M84 S120", wait=True)        # hand the printer back with Marlin's usual idle timeout

    def home(self):
        # Marlin homes X and Y at whatever height Z is at. Go 10 mm up first (relative: the
        # height may not be known yet), so homing never drags the probe across the board.
        self.send(f"G91\nG1 Z10 F{self.pc['feed_z']:.0f}\nG90", wait=True)
        self.send("G28", wait=True)
        self.pos = self.where()

    def lift(self):
        """Lift from the REAL height (M114 first), so it only ever goes up."""
        self.pos = self.where(strict=True)
        super().lift()

    def where(self, strict: bool = False):
        """M114 after M400: 'X:10.00 Y:20.00 Z:5.00 E:0.00 Count ...'.
        strict: raise if the printer does not report a position (never fall back to the
        remembered one; a move must not start from a guess)."""
        self.send("M400")
        self.ser.write(b"M114\n")
        pos, deadline = None, time.time() + 5
        while time.time() < deadline:
            resp = self.ser.readline().decode(errors="replace").strip()
            m = re.search(r"X:\s*(-?[\d.]+)\s+Y:\s*(-?[\d.]+)\s+Z:\s*(-?[\d.]+)", resp)
            if m and pos is None:               # first X: Y: Z: on the line, not "Count X:"
                pos = tuple(float(v) for v in m.groups())
            if resp.startswith("ok"):
                break
        if pos is not None:
            self.pos = pos
        elif strict:
            raise RuntimeError("the printer did not report its position (M114): not moving")
        return self.pos

    def close(self):
        self.ser.close()


class MoonrakerPrinter(Printer):
    """Klipper via Moonraker's HTTP API. The request returns when the G-code is done."""
    blocking = True

    def connect(self):
        with urllib.request.urlopen(self.pc["url"].rstrip("/") + "/printer/info", timeout=5) as r:
            info = json.load(r)
        print(f"[printer] moonraker {info.get('result', {}).get('state', '?')}")

    def send(self, gcode, wait=False):
        script = gcode + ("\nM400" if wait else "")
        q = urllib.parse.urlencode({"script": script})
        url = self.pc["url"].rstrip("/") + "/printer/gcode/script?" + q
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=300) as r:
            r.read()

    def home(self):
        self.send("G28", wait=True)
        self.pos = None


class BambuMqttPrinter(Printer):
    """Bambu printer over its local MQTT broker (port 8883, TLS, user 'bblp').

    Needs on the printer: LAN Only mode ON and Developer Mode ON.
    Sends raw G-code with the 'gcode_line' command. Bambu does not report when a
    move has finished, so move_to() waits the computed travel time * 1.2 + 0.4 s.
    """

    def connect(self):
        import paho.mqtt.client as mqtt
        self._status = {}
        self._got_report = threading.Event()
        self._seq = 0
        sn = self.pc["serial"]
        self._req = f"device/{sn}/request"
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"emcscan-{int(time.time())}")
        c.username_pw_set("bblp", str(self.pc["access_code"]))
        ctx = ssl.create_default_context()
        ctx.check_hostname = False               # the printer uses a self-signed certificate
        ctx.verify_mode = ssl.CERT_NONE
        c.tls_set_context(ctx)

        def on_connect(client, userdata, flags, rc, props=None):
            client.subscribe(f"device/{sn}/report")

        def on_message(client, userdata, msg):
            try:
                data = json.loads(msg.payload)
            except ValueError:
                return
            self._status.update(data.get("print", {}))
            self._got_report.set()

        c.on_connect = on_connect
        c.on_message = on_message
        c.connect(self.pc["host"], 8883, keepalive=30)
        c.loop_start()
        self.client = c
        self._publish({"pushing": {"sequence_id": "0", "command": "pushall"}})
        if not self._got_report.wait(15):
            raise ConnectionError(
                "no status from the printer in 15 s: check IP, serial, access code, "
                "LAN Only mode and Developer Mode")
        print(f"[printer] bambu {self.pc['host']} state={self._status.get('gcode_state', '?')} "
              f"nozzle={self._status.get('nozzle_temper', '?')}C")

    def _publish(self, payload: dict):
        self.client.publish(self._req, json.dumps(payload), qos=1).wait_for_publish(10)

    def send(self, gcode, wait=False):
        self._seq += 1
        self._publish({"print": {"sequence_id": str(self._seq), "command": "gcode_line",
                                 "param": gcode.strip() + "\n"}})

    def _wait_for_motion(self, start, end, feed):
        if start is None or end is None:
            self._sleep_worst_case()
            return
        t = move_time(math.dist(start, end), feed, self.pc["accel"])
        time.sleep(t * 1.2 + 0.4)

    def status(self) -> dict:
        return dict(self._status)

    def close(self):
        self.client.loop_stop()
        self.client.disconnect()


def make_printer(cfg) -> Printer:
    kind = cfg["printer"]["type"]
    return {
        "sim": SimPrinter,
        "bambu_mqtt": BambuMqttPrinter,
        "serial_gcode": SerialGcodePrinter,
        "moonraker": MoonrakerPrinter,
    }[kind](cfg)
