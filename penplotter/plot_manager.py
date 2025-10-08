"""Background plotting manager."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from .config import PlotterSettings
from .grbl_controller import GRBLController, GRBLConnectionError
from .toolpath import PenToolpath

StatusCallback = Callable[[str], None]
ProgressCallback = Callable[[float], None]


@dataclass
class PlotJob:
    toolpaths: Iterable[PenToolpath]


class PlotManager:
    """Runs pen plotting jobs in a background thread."""

    def __init__(
        self,
        controller: GRBLController,
        settings: PlotterSettings,
        *,
        status_cb: Optional[StatusCallback] = None,
        progress_cb: Optional[ProgressCallback] = None,
    ) -> None:
        self.controller = controller
        self.settings = settings
        self.status_cb = status_cb or (lambda message: None)
        self.progress_cb = progress_cb or (lambda progress: None)

        self._job_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._current_job: Optional[PlotJob] = None
        self._state = "idle"

    # ------------------------------------------------------------------
    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def state(self) -> str:
        return self._state

    # ------------------------------------------------------------------
    def start(self, job: PlotJob) -> None:
        with self._job_lock:
            if self.is_running:
                raise RuntimeError("A job is already running")
            self._stop_event.clear()
            self._pause_event.clear()
            self._current_job = job
            self._thread = threading.Thread(target=self._run_job, daemon=True)
            self._thread.start()
            self._state = "running"
            self.status_cb("Job started")
            self.progress_cb(0.0)

    def stop(self) -> None:
        self._stop_event.set()
        self._pause_event.clear()
        self._state = "stopping"
        self.status_cb("Job stopping ...")

    def pause(self) -> None:
        if not self.is_running:
            return
        self._pause_event.set()
        self._state = "paused"
        self.status_cb("Job paused")

    def resume(self) -> None:
        if not self.is_running:
            return
        self._pause_event.clear()
        self._state = "running"
        self.status_cb("Job resumed")

    # ------------------------------------------------------------------
    def _run_job(self) -> None:
        assert self._current_job is not None
        toolpaths = list(self._current_job.toolpaths)
        total_polylines = sum(len(tp.polylines) for tp in toolpaths)
        completed = 0

        try:
            for toolpath in toolpaths:
                if self._stop_event.is_set():
                    break
                try:
                    self.controller.send_command(f"F{toolpath.pen.feed_rate}")
                except GRBLConnectionError as exc:
                    self.status_cb(f"Device error: {exc}")
                    self._state = "error"
                    return
                for polyline in toolpath.polylines:
                    if self._stop_event.is_set():
                        break
                    self._wait_if_paused()
                    try:
                        self._run_polyline(polyline, toolpath.pen.feed_rate)
                    except GRBLConnectionError as exc:
                        self.status_cb(f"Device error: {exc}")
                        self._state = "error"
                        return
                    completed += 1
                    self.progress_cb(completed / max(total_polylines, 1))
        finally:
            self.controller.pen_up()
            self._state = "idle"
            self._current_job = None
            self.status_cb("Job finished" if not self._stop_event.is_set() else "Job stopped")
            self.progress_cb(1.0 if not self._stop_event.is_set() else 0.0)
            self._stop_event.clear()
            self._pause_event.clear()

    def _wait_if_paused(self) -> None:
        while self._pause_event.is_set() and not self._stop_event.is_set():
            time.sleep(0.1)

    def _run_polyline(self, polyline, feed_rate: int) -> None:
        start_x, start_y = polyline[0]
        self.controller.pen_up()
        self.controller.rapid_move(start_x, start_y)
        self.controller.pen_down()
        for x, y in polyline[1:]:
            if self._stop_event.is_set():
                break
            self.controller.linear_move(x, y, feed=feed_rate)
        self.controller.pen_up()
