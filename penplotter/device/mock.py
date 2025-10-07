"""In-memory mock plotter used for development and unit tests."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Tuple, List

from .grbl import Config

XY = Tuple[float, float]


@dataclass
class MockPlotter:
    """Small simulation that mimics the :class:`GRBL` API."""

    cfg: Config = Config()

    def __post_init__(self) -> None:
        self.position: XY = (0.0, 0.0)
        self.pen_pos: float = 1.0
        self.path: List[XY] = []
        self.connected = False

    # Connection ---------------------------------------------------------
    def connect(self) -> "MockPlotter":
        self.connected = True
        return self

    def close(self) -> None:
        self.connected = False

    # Status -------------------------------------------------------------
    def ensure_wpos(self) -> None:
        pass

    def status(self) -> dict:
        return {"state": "IDLE", "wpos": (*self.position, 0.0)}

    def is_idle(self) -> bool:
        return True

    def wait_idle(self, timeout: float = 30.0, poll: float = 0.05) -> None:
        time.sleep(min(poll, 0.01))

    # Motion -------------------------------------------------------------
    def move_xy(self, x: Optional[float] = None, y: Optional[float] = None, *, feed: Optional[int] = None, wait: bool = True):
        nx = self.position[0] if x is None else float(x)
        ny = self.position[1] if y is None else float(y)
        self.position = (nx, ny)
        self.path.append(self.position)
        return []

    def draw_xy(self, x: float, y: float, wait: bool = False):
        return self.move_xy(x, y, wait=wait)

    def jog(self, dx: float = 0.0, dy: float = 0.0, *, feed: Optional[int] = None, wait: bool = True):
        return self.move_xy(self.position[0] + dx, self.position[1] + dy, wait=wait)

    # Pen control -------------------------------------------------------
    def pen_set(self, pos: float, *, step: Optional[float] = None, step_delay_s: float = 0.03, wait: bool = False):
        self.pen_pos = float(max(0.0, min(1.0, pos)))

    def pen_up(self, *, step: Optional[float] = None, step_delay_s: float = 0.03, wait: bool = False):
        self.pen_set(1.0)

    def pen_down(self, *, step: Optional[float] = None, step_delay_s: float = 0.03, wait: bool = False):
        self.pen_set(0.0)

    def pen_lift(self, delta: float, *, step: Optional[float] = None, step_delay_s: float = 0.03, wait: bool = False):
        self.pen_set(self.pen_pos + delta)

    def compensated_pos(self, x: float, y: float, pos_offset: float = 0.0) -> float:
        return max(0.0, min(1.0, 1.0 + pos_offset))

    # Convenience -------------------------------------------------------
    def sweep_rect(self, w: float, h: float, x0: float = 0.0, y0: float = 0.0, *, close: bool = True, wait: bool = True):
        pts = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
        if close:
            pts.append((x0, y0))
        for p in pts:
            self.move_xy(*p)

    def travel_to(self, x: float, y: float, lift: bool = True):
        if lift:
            self.pen_up()
        self.move_xy(x, y)

    def set_origin_here(self):
        self.position = (0.0, 0.0)

    def set_bed(self, x_max: float, y_max: float):
        self.cfg.x_max = float(x_max)
        self.cfg.y_max = float(y_max)

    def set_bed_fixed(self, w: float, h: float):
        self.set_bed(w, h)

    def goto_abs(self, x: float, y: float):
        self.travel_to(x, y)

    def goto_center(self):
        self.travel_to(self.cfg.x_max / 2.0, self.cfg.y_max / 2.0)

    def cmd(self, gcode: str, wait_ok: bool = True):
        return []

    def flush_input(self) -> None:
        pass
