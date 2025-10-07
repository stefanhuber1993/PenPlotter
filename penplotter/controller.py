"""High level orchestration for the PenPlotter server and CLI."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .geometry import Pattern
from .rendering import PlotRenderer, RenderOptions
from .device import MockPlotter


@dataclass
class PlotterState:
    job_state: str = "idle"  # idle | running | cancelling | error
    last_error: Optional[str] = None
    progress_current: int = 0
    progress_total: int = 0
    last_status: Optional[str] = None


@dataclass
class PlotterController:
    """Coordinate patterns, rendering and device IO."""

    device: Any = field(default_factory=MockPlotter)
    renderer_options: RenderOptions = field(default_factory=RenderOptions)

    def __post_init__(self) -> None:
        self.pattern = Pattern()
        self.renderer = PlotRenderer(self.device, options=self.renderer_options)
        self._lock = threading.Lock()
        self._state = PlotterState()
        self._job_thread: Optional[threading.Thread] = None
        self._stop_event: Optional[threading.Event] = None

    # ------------------------------------------------------------------
    # Device lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        self.device.connect()
        try:
            self.device.ensure_wpos()
        except Exception:
            # Not all backends support it (e.g. MockPlotter)
            pass

    def close(self) -> None:
        try:
            self.device.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Pattern management
    # ------------------------------------------------------------------
    def set_pattern(self, pattern: Pattern) -> None:
        with self._lock:
            self.pattern = pattern

    def load_pattern_dict(self, data: Dict[str, Any]) -> Pattern:
        pattern = Pattern.from_dict(data)
        self.set_pattern(pattern)
        return pattern

    def pattern_summary(self) -> Dict[str, Any]:
        with self._lock:
            pat = self.pattern.clone()
        bbox = pat.bounding_box()
        bbox_list = None
        if bbox is not None:
            (x0, y0), (x1, y1) = bbox
            bbox_list = [[x0, y0], [x1, y1]]
        total = pat.total_length()
        return {
            "count": len(pat.items),
            "total_length_mm": total,
            "bounding_box": bbox_list,
        }

    def pattern_strokes(self) -> Dict[str, Any]:
        with self._lock:
            return {"strokes": self.pattern.strokes()}

    # ------------------------------------------------------------------
    # Job control
    # ------------------------------------------------------------------
    def start_job(self, *, options: Optional[Dict[str, Any]] = None) -> None:
        options = options or {}
        with self._lock:
            if self._job_thread and self._job_thread.is_alive():
                raise RuntimeError("Job already running")
            pattern = self.pattern.clone()
            self._state = PlotterState(job_state="running")
            stop_event = threading.Event()
            self._stop_event = stop_event

        def progress_cb(done: int, extra: Dict[str, Any]) -> None:
            with self._lock:
                self._state.progress_current = done
                self._state.progress_total = int(extra.get("total", 0))

        def status_cb(msg: str) -> None:
            with self._lock:
                self._state.last_status = msg

        def worker() -> None:
            nonlocal pattern
            try:
                self.renderer.run(
                    pattern,
                    optimize=options.get("optimize"),
                    resample=options.get("resample"),
                    combine=options.get("combine"),
                    return_home=options.get("return_home", True),
                    pen_filter=options.get("pen_filter"),
                    stop_event=stop_event,
                    progress_cb=progress_cb,
                    status_cb=status_cb,
                )
                with self._lock:
                    self._state.job_state = "idle" if not stop_event.is_set() else "cancelled"
            except RuntimeError as exc:  # pragma: no cover - runtime safety
                if stop_event.is_set() and "cancelled" in str(exc).lower():
                    with self._lock:
                        self._state.job_state = "cancelled"
                        self._state.last_error = None
                else:
                    with self._lock:
                        self._state.job_state = "error"
                        self._state.last_error = str(exc)
            except Exception as exc:  # pragma: no cover - runtime safety
                with self._lock:
                    self._state.job_state = "error"
                    self._state.last_error = str(exc)
            finally:
                with self._lock:
                    self._stop_event = None

        thread = threading.Thread(target=worker, daemon=True)
        self._job_thread = thread
        thread.start()

    def stop_job(self) -> None:
        with self._lock:
            if not self._stop_event:
                return
            self._state.job_state = "cancelling"
            self._stop_event.set()

    def job_status(self) -> Dict[str, Any]:
        with self._lock:
            state = self._state
            thread_alive = self._job_thread.is_alive() if self._job_thread else False
            return {
                "job_state": state.job_state,
                "thread_alive": thread_alive,
                "progress": {
                    "current": state.progress_current,
                    "total": state.progress_total,
                },
                "last_status": state.last_status,
                "last_error": state.last_error,
            }

    # ------------------------------------------------------------------
    # Manual device helpers
    # ------------------------------------------------------------------
    def goto(self, x: float, y: float) -> None:
        self.device.travel_to(x, y, lift=True)

    def jog(self, dx: float, dy: float) -> None:
        self.device.jog(dx=dx, dy=dy)

    def pen_up(self) -> None:
        self.device.pen_up()

    def pen_down(self) -> None:
        self.device.pen_down()

    def pen_height(self, pos: float) -> None:
        self.device.pen_set(pos)

    def set_origin_here(self) -> None:
        self.device.set_origin_here()

    def device_status(self) -> Dict[str, Any]:
        try:
            status = self.device.status()
        except Exception as exc:  # pragma: no cover - runtime safety
            status = {"state": "error", "error": str(exc)}
        else:
            wpos = status.get("wpos")
            if isinstance(wpos, tuple):
                status = dict(status)
                status["wpos"] = list(wpos)
        return status


__all__ = ["PlotterController", "PlotterState"]
