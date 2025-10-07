"""GRBL device helper.

This module contains a lightly modernised version of the original ``penplot_helper``
logic.  The functionality is identical but lives inside the ``penplotter.device``
package so it can be imported without pulling legacy notebook helpers.
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

try:
    import serial  # type: ignore
except ImportError as exc:  # pragma: no cover - handled at runtime
    raise RuntimeError("pyserial is required. Install with: pip install pyserial") from exc


_STATUS_STATE = re.compile(r"^<\s*([A-Za-z]+)(?=[|,>])")
_STATUS_WPOS = re.compile(r"WPos:([^|>]+)")
_STATUS_MPOS = re.compile(r"MPos:([^|>]+)")
_STATUS_WCO = re.compile(r"WCO:([^|>]+)")

XY = Tuple[float, float]


@dataclass
class Config:
    # Serial
    port: str = "/dev/tty.usbserial-A50285BI"
    baudrate: int = 115200
    read_timeout_s: float = 1.0

    # Workspace (mm)
    x_max: float = 300.0
    y_max: float = 245.0

    # Feeds (mm/min)
    feed_travel: int = 3000
    feed_draw: int = 3000

    # Servo calibration (pos=0 -> down, pos=1 -> up)
    s_down: int = 90
    s_up: int = 40
    servo_travel_deg: float = 80.0

    # Safety
    clip_to_bed: bool = True


@dataclass
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    def clamp_point(self, x: float, y: float) -> XY:
        return (min(max(self.x0, x), self.x1), min(max(self.y0, y), self.y1))

    @property
    def cx(self) -> float:
        return 0.5 * (self.x0 + self.x1)

    @property
    def cy(self) -> float:
        return 0.5 * (self.y0 + self.y1)


@dataclass
class Compensation:
    """Bilinear pen-height compensation across a rectangular area."""

    area: Rect
    hBL: float  # (x0,y0)
    hBR: float  # (x1,y0)
    hTL: float  # (x0,y1)
    hTR: float  # (x1,y1)

    def height_at(self, x: float, y: float) -> float:
        x = min(max(x, self.area.x0), self.area.x1)
        y = min(max(y, self.area.y0), self.area.y1)
        dx = self.area.x1 - self.area.x0
        dy = self.area.y1 - self.area.y0
        if dx <= 0 or dy <= 0:
            return self.hBL
        u = (x - self.area.x0) / dx
        v = (y - self.area.y0) / dy
        return (
            (1 - u) * (1 - v) * self.hBL
            + u * (1 - v) * self.hBR
            + (1 - u) * v * self.hTL
            + u * v * self.hTR
        )

    @staticmethod
    def from_widget_state(state: Dict) -> "Compensation":
        cs = state["corners"]
        rect = Rect(
            float(cs["BL"]["x"]),
            float(cs["BL"]["y"]),
            float(cs["TR"]["x"]),
            float(cs["TR"]["y"]),
        )
        return Compensation(
            area=rect,
            hBL=float(cs["BL"]["h"]),
            hBR=float(cs["BR"]["h"]),
            hTL=float(cs["TL"]["h"]),
            hTR=float(cs["TR"]["h"]),
        )


class GRBL:
    """Minimal GRBL wrapper used by the renderer and HTTP API."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.ser: Optional[serial.Serial] = None
        self._pen_pos: float = 1.0  # track last commanded position [0..1], default up
        self._comp: Optional[Compensation] = None

    # -------- Connection / basic I/O --------
    def connect(self) -> "GRBL":
        self.ser = serial.Serial(
            self.cfg.port, baudrate=self.cfg.baudrate, timeout=self.cfg.read_timeout_s
        )
        time.sleep(2.0)
        self._writeln("\r\n")  # wake
        self.flush_input()
        self.cmd("G90")
        self.cmd("G21")
        return self

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()

    def _writeln(self, s: str) -> None:
        if not s.endswith("\n"):
            s += "\n"
        assert self.ser is not None
        self.ser.write(s.encode())
        self.ser.flush()

    def _readlines_until_timeout(self) -> List[str]:
        assert self.ser is not None
        lines: List[str] = []
        t0 = time.time()
        while True:
            line = self.ser.readline().decode(errors="ignore").strip()
            if line:
                lines.append(line)
                if line.lower().startswith("ok"):
                    break
            elif time.time() - t0 > self.cfg.read_timeout_s:
                break
        return lines

    def cmd(self, gcode: str, wait_ok: bool = True) -> List[str]:
        self._writeln(gcode)
        return self._readlines_until_timeout() if wait_ok else []

    def flush_input(self) -> None:
        if self.ser:
            self.ser.reset_input_buffer()

    # -------- Status / idle waiting --------
    def ensure_wpos(self) -> None:
        self.cmd("$10=3")  # bit0=MPos, bit1=WPos
        for _ in range(3):
            _ = self.status()
            time.sleep(0.05)

    def status(self) -> Dict[str, Optional[object]]:
        assert self.ser is not None
        self.ser.write(b"?")
        self.ser.flush()
        line = self.ser.readline().decode(errors="ignore").strip()

        state = None
        wpos = None
        m = _STATUS_STATE.search(line)
        if m:
            state = m.group(1)

        m_w = _STATUS_WPOS.search(line)
        if m_w:
            wpos = tuple(float(v) for v in m_w.group(1).split(",")[:3])
        else:
            m_m = _STATUS_MPOS.search(line)
            if m_m:
                mpos = [float(v) for v in m_m.group(1).split(",")[:3]]
                m_c = _STATUS_WCO.search(line)
                if m_c:
                    wco = [float(v) for v in m_c.group(1).split(",")[:3]]
                    wpos = tuple(mp - wc for mp, wc in zip(mpos, wco))
                else:
                    wpos = tuple(mpos)

        return {"raw": line, "state": state, "wpos": wpos}

    def is_idle(self) -> bool:
        try:
            s = self.status().get("state", None)
            return (s or "").upper() == "IDLE"
        except Exception:
            return False

    def wait_idle(self, timeout: float = 30.0, poll: float = 0.05) -> None:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.is_idle():
                return
            time.sleep(poll)
        raise TimeoutError("GRBL did not become IDLE in time.")

    # -------- Movement / coordinates --------
    def move_xy(
        self,
        x: Optional[float] = None,
        y: Optional[float] = None,
        *,
        feed: Optional[int] = None,
        wait: bool = True,
    ) -> List[str]:
        if feed is None:
            feed = self.cfg.feed_travel
        parts = ["G0"]
        if x is not None:
            parts.append(f"X{self._clip_x(x):.3f}")
        if y is not None:
            parts.append(f"Y{self._clip_y(y):.3f}")
        parts.append(f"F{feed}")
        out = self.cmd(" ".join(parts))
        if wait:
            self.wait_idle()
        return out

    def draw_xy(self, x: float, y: float, wait: bool = False) -> List[str]:
        """Move in drawing mode."""

        out = self.cmd(
            f"G1 X{self._clip_x(x):.3f} Y{self._clip_y(y):.3f} F{self.cfg.feed_draw}"
        )
        if wait:
            self.wait_idle()
        return out

    def jog(
        self,
        dx: float = 0.0,
        dy: float = 0.0,
        *,
        feed: Optional[int] = None,
        wait: bool = True,
    ) -> List[str]:
        if feed is None:
            feed = self.cfg.feed_travel
        self.cmd("G91")
        self.cmd(f"G0 X{dx:.3f} Y{dy:.3f} F{feed}")
        out = self.cmd("G90")
        if wait:
            self.wait_idle()
        return out

    # -------- Compensation --------
    def set_compensation(self, comp: Compensation) -> None:
        self._comp = comp

    def set_compensation_from_widget(self, widget_state: Dict) -> None:
        self._comp = Compensation.from_widget_state(widget_state)

    @staticmethod
    def _apply_pos_offset(base: float, pos_offset: float) -> float:
        v = base + float(pos_offset)
        if v < 0.0:
            print(
                f"[warn] pos_offset drives pen below 0.0 (base {base:.3f} + {pos_offset:.3f}). Clipping to 0.0."
            )
            v = 0.0
        return min(1.0, v)

    def compensated_pos(self, x: float, y: float, pos_offset: float = 0.0) -> float:
        base = self._comp.height_at(x, y) if self._comp is not None else 1.0
        return self._apply_pos_offset(base, pos_offset)

    # -------- Pen control with smooth stepping --------
    def _servo_map(self, pos: float) -> int:
        pos = float(max(0.0, min(1.0, pos)))
        theta_max = math.radians(getattr(self.cfg, "servo_travel_deg", 80.0))
        g = math.asin(pos * math.sin(theta_max)) / theta_max  # 0..1
        s0, s1 = self.cfg.s_down, self.cfg.s_up
        return int(round(s0 + g * (s1 - s0)))

    def _issue_servo(self, pos: float) -> None:
        s = self._servo_map(pos)
        self.cmd(f"M3 S{s}", wait_ok=True)

    def pen_set(
        self,
        pos: float,
        *,
        step: Optional[float] = None,
        step_delay_s: float = 0.03,
        wait: bool = False,
    ) -> None:
        """Set servo to absolute pos in [0..1]."""

        target = float(max(0.0, min(1.0, pos)))
        current = float(max(0.0, min(1.0, self._pen_pos)))

        if step is None or step <= 0.0 or abs(target - current) <= step:
            self._issue_servo(target)
            self._pen_pos = target
        else:
            direction = 1.0 if target > current else -1.0
            p = current
            while True:
                p_next = p + direction * step
                if (direction > 0 and p_next >= target) or (direction < 0 and p_next <= target):
                    break
                self._issue_servo(p_next)
                self._pen_pos = p_next
                time.sleep(step_delay_s)
                p = p_next
            if self._pen_pos != target:
                self._issue_servo(target)
                self._pen_pos = target

        if wait:
            self.wait_idle()

    def pen_up(
        self,
        *,
        step: Optional[float] = None,
        step_delay_s: float = 0.03,
        wait: bool = False,
    ) -> None:
        self.pen_set(1.0, step=step, step_delay_s=step_delay_s, wait=wait)

    def pen_down(
        self,
        *,
        step: Optional[float] = None,
        step_delay_s: float = 0.03,
        wait: bool = False,
    ) -> None:
        self.pen_set(0.0, step=step, step_delay_s=step_delay_s, wait=wait)

    def pen_lift(
        self,
        delta: float,
        *,
        step: Optional[float] = None,
        step_delay_s: float = 0.03,
        wait: bool = False,
    ) -> None:
        target = max(0.0, min(1.0, self._pen_pos + float(delta)))
        self.pen_set(target, step=step, step_delay_s=step_delay_s, wait=wait)

    # -------- Simple motion helpers --------
    def sweep_rect(
        self,
        w: float,
        h: float,
        x0: float = 0.0,
        y0: float = 0.0,
        *,
        close: bool = True,
        wait: bool = True,
    ) -> None:
        self.pen_up(wait=False)
        self.move_xy(x0, y0, wait=False)
        self.move_xy(x0 + w, y0, wait=False)
        self.move_xy(x0 + w, y0 + h, wait=False)
        self.move_xy(x0, y0 + h, wait=False)
        if close:
            self.move_xy(x0, y0, wait=False)
        if wait:
            self.wait_idle()

    def travel_to(self, x: float, y: float, lift: bool = True) -> None:
        if lift:
            self.pen_up()
        self.move_xy(x, y, wait=True)

    def set_origin_here(self) -> None:
        self.cmd("G92 X0 Y0 Z0")

    def set_bed(self, x_max: float, y_max: float) -> None:
        self.cfg.x_max = float(x_max)
        self.cfg.y_max = float(y_max)

    def set_bed_fixed(self, w: float, h: float) -> None:
        self.set_bed(w, h)
        print(f"Bed set: X_MAX={w:.3f} mm, Y_MAX={h:.3f} mm")

    def goto_abs(self, x: float, y: float) -> None:
        self.travel_to(x, y, lift=True)
        print(f"Goto absolute: ({x:.3f}, {y:.3f})")

    def goto_center(self) -> None:
        cx, cy = self.cfg.x_max / 2.0, self.cfg.y_max / 2.0
        self.travel_to(cx, cy, lift=True)
        print(f"Goto center: ({cx:.3f}, {cy:.3f})")

    # -------- Clipping --------
    def _clip_x(self, x: float) -> float:
        return max(0.0, min(self.cfg.x_max, x)) if self.cfg.clip_to_bed else x

    def _clip_y(self, y: float) -> float:
        return max(0.0, min(self.cfg.y_max, y)) if self.cfg.clip_to_bed else y


__all__ = ["Config", "Rect", "Compensation", "GRBL"]
