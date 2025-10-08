"""GRBL controller wrapper used by the NiceGUI application."""

from __future__ import annotations

import threading
import time
from typing import List, Optional

import serial
from serial.tools import list_ports

from .config import PlotterSettings


class GRBLConnectionError(RuntimeError):
    """Raised when a GRBL operation fails due to connectivity issues."""


class GRBLController:
    """Thin wrapper around a GRBL compatible device."""

    def __init__(self, settings: PlotterSettings) -> None:
        self.settings = settings
        self._serial: Optional[serial.Serial] = None
        self._lock = threading.Lock()
        self._connected_port: Optional[str] = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    @staticmethod
    def enumerate_ports() -> List[str]:
        return [p.device for p in list_ports.comports()]

    def connect(self, port: str) -> None:
        with self._lock:
            if self._serial and self._serial.is_open:
                if self._connected_port == port:
                    return
                self.disconnect()
            try:
                self._serial = serial.Serial(
                    port,
                    baudrate=self.settings.baudrate,
                    timeout=self.settings.read_timeout,
                )
            except serial.SerialException as exc:  # pragma: no cover - hardware dependent
                raise GRBLConnectionError(str(exc)) from exc
            self._connected_port = port
            time.sleep(2.0)
            self._write("\r\n")
            self.flush_input()
            self.send_command("G90")  # absolute coordinates
            self.send_command("G21")  # millimeters

    def disconnect(self) -> None:
        with self._lock:
            if self._serial:
                try:
                    self._serial.close()
                finally:
                    self._serial = None
                    self._connected_port = None

    @property
    def is_connected(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    @property
    def connected_port(self) -> Optional[str]:
        return self._connected_port

    # ------------------------------------------------------------------
    # Low level helpers
    # ------------------------------------------------------------------
    def _require_connection(self) -> serial.Serial:
        if not self._serial or not self._serial.is_open:
            raise GRBLConnectionError("Device is not connected")
        return self._serial

    def _write(self, data: str) -> None:
        ser = self._require_connection()
        if not data.endswith("\n"):
            data += "\n"
        ser.write(data.encode("ascii"))
        ser.flush()

    def _read_until_ok(self) -> List[str]:
        ser = self._require_connection()
        lines: List[str] = []
        t0 = time.monotonic()
        while True:
            raw = ser.readline().decode(errors="ignore").strip()
            if raw:
                lines.append(raw)
                if raw.lower().startswith("ok"):
                    break
            if time.monotonic() - t0 > self.settings.read_timeout:
                break
        return lines

    def flush_input(self) -> None:
        ser = self._require_connection()
        ser.reset_input_buffer()

    # ------------------------------------------------------------------
    # High-level API
    # ------------------------------------------------------------------
    def send_command(self, command: str, wait_for_ok: bool = True) -> List[str]:
        with self._lock:
            self._write(command)
            return self._read_until_ok() if wait_for_ok else []

    def rapid_move(self, x: float, y: float) -> None:
        self.send_command(f"G0 X{x:.3f} Y{y:.3f} F{self.settings.travel_feed}")

    def linear_move(self, x: float, y: float, feed: Optional[int] = None) -> None:
        feed_value = feed or self.settings.travel_feed
        self.send_command(f"G1 X{x:.3f} Y{y:.3f} F{feed_value}")

    def pen_up(self) -> None:
        pwm = self.settings.servo.to_pwm(1.0)
        self.send_command(f"M3 S{pwm}")

    def pen_down(self) -> None:
        pwm = self.settings.servo.to_pwm(0.0)
        self.send_command(f"M3 S{pwm}")

    def status(self) -> str:
        ser = self._require_connection()
        with self._lock:
            ser.write(b"?")
            ser.flush()
            return ser.readline().decode(errors="ignore").strip()

    def dwell(self, seconds: float) -> None:
        self.send_command(f"G4 P{max(0.0, seconds):.3f}")
