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
import ssl
import threading
import time
import urllib.parse
import urllib.request

from .plan import move_time


class Printer:
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

    def move_to(self, x: float, y: float, z: float):
        """Move the nozzle. Goes up first, then across, then down, so the nozzle never
        drags diagonally into the board."""
        x, y, z = round(x, 3), round(y, 3), round(z, 3)
        fxy, fz = self.pc["feed_xy"], self.pc["feed_z"]
        cur = self.pos
        if cur is None or z > cur[2]:
            self._line(f"G1 Z{z} F{fz:.0f}", cur, (cur[0], cur[1], z) if cur else None, fz)
            cur = (cur[0], cur[1], z) if cur else None
            self._line(f"G1 X{x} Y{y} F{fxy:.0f}", cur, (x, y, z) if cur else None, fxy)
        else:
            self._line(f"G1 X{x} Y{y} F{fxy:.0f}", cur, (x, y, cur[2]), fxy)
            self._line(f"G1 Z{z} F{fz:.0f}", (x, y, cur[2]), (x, y, z), fz)
        self.pos = (x, y, z)
        time.sleep(self.pc["settle_s"])

    def lift(self):
        """Raise to safe_z (used at the end and on Ctrl+C)."""
        sz = float(self.pc["safe_z"])
        if self.pos is None:
            self.send(f"G91\nG1 Z10 F{self.pc['feed_z']:.0f}\nG90", wait=True)
            self._sleep_worst_case()
            return
        if self.pos[2] < sz:
            self.move_to(self.pos[0], self.pos[1], sz)

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

    def move_to(self, x, y, z):
        settle = self.pc["settle_s"]
        self.pc["settle_s"] = settle * float(self.cfg["sim"]["time_scale"])
        try:
            super().move_to(x, y, z)
        finally:
            self.pc["settle_s"] = settle


class SerialGcodePrinter(Printer):
    """Marlin-style printer on a COM port. Every line waits for 'ok'."""

    def connect(self):
        import serial
        self.ser = serial.Serial(self.pc["port"], int(self.pc["baud"]), timeout=1)
        time.sleep(2.0)                         # many boards reset when the port opens
        self.ser.reset_input_buffer()
        print(f"[printer] serial G-code on {self.pc['port']}")

    def send(self, gcode, wait=False):
        lines = [g for g in gcode.splitlines() if g.strip()]
        if wait:
            lines.append("M400")                # returns 'ok' only when motion is done
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

    def home(self):
        self.send("G28", wait=True)
        self.pos = None

    def close(self):
        self.ser.close()


class MoonrakerPrinter(Printer):
    """Klipper via Moonraker's HTTP API. The request returns when the G-code is done."""

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
