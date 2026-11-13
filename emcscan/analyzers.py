"""Spectrum analyzer drivers.

    TinySA      TinySA Ultra over its USB serial port (text shell, prompt "ch> ")
    SimAnalyzer fake board with configurable noise sources, for demos and tests

Interface:  connect()  set_lna(on)  sweep(band, probe_xyz) -> (freqs_hz, levels_dbm)  close()

TinySA commands used (all plain text, typed exactly as you could in a terminal):
    version                         firmware string
    pause / resume                  stop the screen sweeping while we drive it
    lna on | lna off                built-in ~20 dB preamp (Ultra)
    rbw auto | rbw <kHz>            resolution bandwidth
    scan <start> <stop> <pts> 3     one sweep; prints "<freq> <dBm>" per point
"""
from __future__ import annotations

import math
import time

import numpy as np


class Analyzer:
    lna_on = False

    def __init__(self, cfg):
        self.cfg = cfg
        self.ac = cfg["analyzer"]

    def connect(self): ...
    def set_lna(self, on: bool): self.lna_on = on
    def sweep(self, band: dict, probe_xyz=None) -> tuple[np.ndarray, np.ndarray]: raise NotImplementedError
    def close(self): ...
    def info(self) -> str: return type(self).__name__


class TinySA(Analyzer):
    """Notes from the 2026-11-12 bench test (Ultra+ ZS407, fw v1.4-200):
    - `scan` lines are "<freq> <dBm> 0"; levels come in 0.5 dB steps.
    - About 1 sweep in 40 came back a few points short right after a setting change,
      so a wrong point count is retried once before giving up.
    - The Ultra switches input path at 800 MHz and the noise floor steps down about
      8 dB there, so bands should not straddle 800 MHz (a warning is printed).
    - The firmware cannot report whether the LNA is on, so close() returns the unit
      to its power-on defaults (LNA off, RBW auto) instead of "the user's settings".
    """
    USB_IDS = {(0x0483, 0x5740)}     # STM32 virtual COM port used by TinySA / TinySA Ultra
    PROMPT = b"ch> "
    ULTRA_START_HZ = 800e6           # Ultra input-path switch (firmware default)
    retries = 0

    def connect(self):
        import serial
        from serial.tools import list_ports
        port = self.ac["port"]
        if port in ("", "auto"):
            found = [p.device for p in list_ports.comports() if (p.vid, p.pid) in self.USB_IDS]
            if not found:
                raise ConnectionError("TinySA not found on USB. Plug it in and switch it on, "
                                      "or set analyzer.port to its COM port.")
            port = found[0]
        self.ser = serial.Serial(port, 115200, timeout=0.5)
        self.ser.reset_input_buffer()
        self.ser.write(b"\r")
        self._read_until_prompt(5)
        lines = [ln.strip() for ln in self.cmd("version").split("\n") if ln.strip()]
        self.version = lines[0] if lines else "unknown"
        self.hw = next((ln for ln in lines[1:] if ln.lower().startswith("hw")), "")
        self.port = port
        self._warned = set()
        self.cmd("pause")
        rbw = self.ac["rbw_khz"]
        self.cmd("rbw auto" if str(rbw) == "auto" else f"rbw {rbw}")
        print(f"[analyzer] {self.info()}")

    def _read_until_prompt(self, timeout: float) -> bytes:
        buf = b""
        deadline = time.time() + timeout
        while time.time() < deadline:
            chunk = self.ser.read(4096)
            if chunk:
                buf += chunk
                if buf.endswith(self.PROMPT):
                    return buf[: -len(self.PROMPT)]
        raise TimeoutError(f"TinySA did not answer in {timeout} s (got {buf[-200:]!r})")

    def cmd(self, text: str, timeout: float = 5.0) -> str:
        self.ser.write(text.encode() + b"\r")
        out = self._read_until_prompt(timeout).decode(errors="replace")
        lines = out.replace("\r", "").split("\n")
        if lines and lines[0].strip() == text.strip():      # drop the echoed command
            lines = lines[1:]
        return "\n".join(lines)

    def set_lna(self, on: bool):
        self.cmd("lna on" if on else "lna off")
        self.lna_on = on

    def _check_band(self, band):
        a, b = float(band["start_hz"]), float(band["stop_hz"])
        key = (a, b)
        if a < self.ULTRA_START_HZ < b and key not in getattr(self, "_warned", set()):
            self._warned = getattr(self, "_warned", set()) | {key}
            print(f"[analyzer] warning: band {band.get('name', key)} crosses 800 MHz, where the "
                  f"TinySA Ultra noise floor steps down about 8 dB. Split the band at 800 MHz.")

    def _scan_once(self, band, n):
        out = self.cmd(f"scan {int(band['start_hz'])} {int(band['stop_hz'])} {n} 3", timeout=60)
        freqs, levels = [], []
        for line in out.split("\n"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    freqs.append(float(parts[0])); levels.append(float(parts[1]))
                except ValueError:
                    pass
        return freqs, levels

    def sweep(self, band, probe_xyz=None):
        self._check_band(band)
        n = int(band["points"])
        freqs, levels = self._scan_once(band, n)
        if len(levels) != n:                       # rare short reply: flush and try once more
            self.retries += 1
            print(f"[analyzer] got {len(levels)} of {n} points, retrying the sweep")
            time.sleep(0.1)
            self.ser.reset_input_buffer()
            freqs, levels = self._scan_once(band, n)
        if len(levels) != n:
            raise RuntimeError(f"TinySA returned {len(levels)} points, expected {n} (after a retry)")
        return np.array(freqs), np.array(levels, dtype=np.float32)

    def close(self):
        """Hand the unit back in its power-on state: LNA off, RBW auto, screen sweeping."""
        try:
            for c in ("lna off", "rbw auto", "resume"):
                try:
                    self.cmd(c)
                except Exception:
                    pass
            self.lna_on = False
        finally:
            self.ser.close()

    def info(self):
        hw = f", {self.hw}" if getattr(self, "hw", "") else ""
        return f"{self.version}{hw} on {self.port}"


class SimAnalyzer(Analyzer):
    """A fake board. Each [[sim.sources]] entry is a radiating spot:
        x, y      position on the board (plate frame, mm)
        freq_hz   fundamental; harmonics 2..harmonics fall off by 6 dB each
        level_dbm level seen by the probe 1 mm above the spot
    Field falls off with distance like a small loop (60 dB per decade far out)."""

    def connect(self):
        self.rng = np.random.default_rng(1)
        print("[analyzer] simulator")

    def sweep(self, band, probe_xyz=None):
        n = int(band["points"])
        f = np.linspace(band["start_hz"], band["stop_hz"], n)
        floor = float(self.cfg["sim"]["noise_floor_dbm"]) - (16.0 if self.lna_on else 0.0)
        p_mw = 10 ** ((floor + self.rng.normal(0, 1.5, n)) / 10)
        rbw = max((band["stop_hz"] - band["start_hz"]) / n * 2.0, 100e3)
        x, y, h = probe_xyz if probe_xyz is not None else (0, 0, 10)
        for s in self.cfg["sim"]["sources"]:
            r = math.sqrt((x - s["x"]) ** 2 + (y - s["y"]) ** 2 + h ** 2)
            near = s["level_dbm"] - 20 * math.log10(1 + (r / s.get("size_mm", 3.0)) ** 3)
            for k in range(1, int(s.get("harmonics", 1)) + 1):
                fk = s["freq_hz"] * k
                if band["start_hz"] - rbw < fk < band["stop_hz"] + rbw:
                    lvl = near - 6.0 * (k - 1)
                    p_mw += 10 ** (lvl / 10) * np.exp(-0.5 * ((f - fk) / (rbw / 2.355)) ** 2)
        time.sleep(0.25 * float(self.cfg["sim"]["time_scale"]))
        return f, (10 * np.log10(p_mw)).astype(np.float32)


def make_analyzer(cfg) -> Analyzer:
    return {"sim": SimAnalyzer, "tinysa": TinySA}[cfg["analyzer"]["type"]](cfg)
