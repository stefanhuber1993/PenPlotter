"""Rendering helpers for executing a :class:`penplotter.geometry.Pattern`.

The previous notebook-centric workflow exposed the :class:`Renderer` class only
through an ipywidget bridge.  The new module keeps the proven motion logic but
removes any UI coupling so it can be shared by the HTTP API, command line tools
and future integrations.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from threading import Event
from typing import Iterable, Optional, Union, Callable, Dict, Any

from .geometry import Pattern, Polyline, XY

ProgressCallback = Callable[[int, Dict[str, Any]], None]
StatusCallback = Callable[[str], None]

DEFAULT_PEN_COLORS = {
    0: "#111111",
    1: "#e41a1c",
    2: "#377eb8",
    3: "#4daf4a",
    4: "#984ea3",
    5: "#ff7f00",
    6: "#a65628",
    7: "#f781bf",
    8: "#999999",
}


@dataclass
class RenderOptions:
    z_mode: str = "per_segment"
    z_threshold: float = 0.02
    settle_down_s: float = 0.05
    settle_up_s: float = 0.03
    z_step: Optional[float] = None
    z_step_delay: float = 0.03
    flush_every: int = 200
    feed_travel: Optional[int] = None
    lift_delta: float = 0.2


class PlotRenderer:
    """Execute a :class:`Pattern` on a GRBL-like device."""

    def __init__(
        self,
        grbl,
        *,
        options: Optional[RenderOptions] = None,
        pen_colors: Optional[dict] = None,
    ) -> None:
        self.g = grbl
        self.options = options or RenderOptions()
        self.pen_colors = dict(DEFAULT_PEN_COLORS)
        if pen_colors:
            self.pen_colors.update(pen_colors)
        self._cur_xy: Optional[XY] = None

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def preview_strokes(self, pattern: Pattern, *, pens: Optional[Union[int, Iterable[int]]] = None,
                        width: float = 1.5) -> Dict[str, Any]:
        pens_set = None
        if pens is not None:
            pens_set = set([pens] if isinstance(pens, int) else list(pens))
        strokes = []
        for it in pattern.items:
            if pens_set is not None and it.pen_id not in pens_set:
                continue
            pts = list(reversed(it.pts)) if it._rev else list(it.pts)
            if len(pts) < 2:
                continue
            color = self.pen_colors.get(it.pen_id, DEFAULT_PEN_COLORS[0])
            strokes.append(
                {
                    "pts": pts,
                    "color": color,
                    "width": width,
                    "pen": f"pen{it.pen_id}",
                }
            )
        return {"strokes": strokes}

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def run(
        self,
        pattern: Pattern,
        *,
        start_xy: XY = (0.0, 0.0),
        optimize: Optional[str] = None,
        resample: Optional[dict] = None,
        combine: Optional[dict] = None,
        return_home: bool = True,
        pen_filter: Optional[Union[int, Iterable[int]]] = None,
        stop_event: Optional[Event] = None,
        progress_cb: Optional[ProgressCallback] = None,
        status_cb: Optional[StatusCallback] = None,
    ) -> None:
        """Execute the pattern and optionally report progress."""

        opts = self.options

        if combine is not None:
            msg = pattern.combine_endpoints(join_tol_mm=combine.get("join_tol_mm", 0.05))
            if status_cb:
                status_cb(msg)
            else:
                print(msg)

        if resample is not None:
            msg = pattern.resample_polylines(
                max_dev_mm=resample.get("max_dev_mm", None),
                max_seg_mm=resample.get("max_seg_mm", None),
            )
            if status_cb:
                status_cb(msg)
            else:
                print(msg)

        if optimize == "nn":
            before = self._travel_estimate(pattern, start_xy)
            pattern.optimize_order_nn(start_xy=start_xy)
            after = self._travel_estimate(pattern, start_xy)
            gain = max(0.0, before - after)
            pct = (gain / before * 100.0) if before > 0 else 0.0
            msg = (
                f"Optimize order: nn, travel {before:.2f} -> {after:.2f} mm, "
                f"saved {gain:.2f} mm ({pct:.1f} percent)."
            )
            if status_cb:
                status_cb(msg)
            else:
                print(msg)

        pens_set = None
        if pen_filter is not None:
            pens_set = set([pen_filter] if isinstance(pen_filter, int) else list(pen_filter))

        exec_items = []
        for it in pattern.items:
            if isinstance(it, Polyline):
                if pens_set is None or it.pen_id in pens_set:
                    exec_items.append(it)
            else:
                raise TypeError(f"Unsupported item at execution: {type(it).__name__}")

        self._cur_xy = start_xy

        total_segments = sum(max(0, len(it.pts) - 1) for it in exec_items)
        progressed = 0

        def report_progress(delta: int) -> None:
            nonlocal progressed
            progressed += delta
            if progress_cb:
                progress_cb(progressed, {"total": total_segments})

        for it in exec_items:
            self._run_polyline(it, opts, stop_event=stop_event, report_progress=report_progress)

        if return_home:
            self.g.pen_set(1.0, step=opts.z_step, step_delay_s=opts.z_step_delay, wait=False)
            if opts.settle_up_s > 0:
                time.sleep(opts.settle_up_s)
            feed = opts.feed_travel if opts.feed_travel is not None else getattr(self.g.cfg, "feed_travel", None)
            self.g.move_xy(0.0, 0.0, feed=feed, wait=True)
            self._cur_xy = (0.0, 0.0)

    # ------------------------------------------------------------------
    # runners
    # ------------------------------------------------------------------
    def _run_polyline(
        self,
        it: Polyline,
        opts: RenderOptions,
        *,
        stop_event: Optional[Event],
        report_progress: Callable[[int], None],
    ) -> None:
        if len(it.pts) < 2:
            return
        pts = list(reversed(it.pts)) if it._rev else list(it.pts)

        # travel to start at feed_travel
        x0, y0 = pts[0]
        self._travel_to(x0, y0, opts)

        # per-shape draw feed
        if it.feed_draw is not None and hasattr(self.g, "cfg"):
            try:
                self.g.cfg.feed_draw = int(it.feed_draw)
            except Exception:
                pass

        # choose z per renderer mode
        if opts.z_mode in ("start", "centroid"):
            if opts.z_mode == "start":
                zx, zy = pts[0]
            else:
                zx = sum(p[0] for p in pts) / len(pts)
                zy = sum(p[1] for p in pts) / len(pts)
            z = self._pen_pos(zx, zy, it.pen_pressure)
            self._pen_set(z, opts, settle=True)
            for i in range(1, len(pts)):
                if stop_event and stop_event.is_set():
                    raise RuntimeError("Render cancelled")
                ex, ey = pts[i]
                self.g.draw_xy(ex, ey, wait=False)
                if i % opts.flush_every == 0:
                    self.g.wait_idle()
                report_progress(1)

        elif opts.z_mode == "per_segment":
            sx, sy = pts[0]
            ex, ey = pts[1]
            mx, my = 0.5 * (sx + ex), 0.5 * (sy + ey)
            z = self._pen_pos(mx, my, it.pen_pressure)
            self._pen_set(z, opts, settle=True)
            for i in range(1, len(pts)):
                if stop_event and stop_event.is_set():
                    raise RuntimeError("Render cancelled")
                sx, sy = pts[i - 1]
                ex, ey = pts[i]
                mx, my = 0.5 * (sx + ex), 0.5 * (sy + ey)
                z = self._pen_pos(mx, my, it.pen_pressure)
                self._pen_set(z, opts, settle=False)
                self.g.draw_xy(ex, ey, wait=False)
                if i % opts.flush_every == 0:
                    self.g.wait_idle()
                report_progress(1)

        elif opts.z_mode == "threshold":
            sx, sy = pts[0]
            ex, ey = pts[1]
            mx, my = 0.5 * (sx + ex), 0.5 * (sy + ey)
            cur_z = self._pen_pos(mx, my, it.pen_pressure)
            self._pen_set(cur_z, opts, settle=True)
            for i in range(1, len(pts)):
                if stop_event and stop_event.is_set():
                    raise RuntimeError("Render cancelled")
                sx, sy = pts[i - 1]
                ex, ey = pts[i]
                mx, my = 0.5 * (sx + ex), 0.5 * (sy + ey)
                z = self._pen_pos(mx, my, it.pen_pressure)
                if abs(z - cur_z) > opts.z_threshold:
                    self._pen_set(z, opts, settle=False)
                    cur_z = z
                self.g.draw_xy(ex, ey, wait=False)
                if i % opts.flush_every == 0:
                    self.g.wait_idle()
                report_progress(1)
        else:
            raise ValueError(f"Unknown z_mode: {opts.z_mode}")

        self.g.wait_idle()
        self._partial_lift_to_travel(pts[-1], opts)
        self._cur_xy = pts[-1]

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    def _pen_pos(self, x: float, y: float, pen_pressure: float) -> float:
        return self.g.compensated_pos(x, y, pos_offset=pen_pressure)

    def _pen_set(self, target: float, opts: RenderOptions, *, settle: bool):
        self.g.pen_set(target, step=opts.z_step, step_delay_s=opts.z_step_delay, wait=False)
        if settle and opts.settle_down_s > 0:
            time.sleep(opts.settle_down_s)

    def _partial_lift_to_travel(self, xy: XY, opts: RenderOptions):
        x, y = xy
        base = self._pen_pos(x, y, 0.0)
        target = min(1.0, base + opts.lift_delta)
        self.g.pen_set(target, step=opts.z_step, step_delay_s=opts.z_step_delay, wait=False)
        if opts.settle_up_s > 0:
            time.sleep(opts.settle_up_s)

    def _travel_to(self, x: float, y: float, opts: RenderOptions):
        feed = opts.feed_travel if opts.feed_travel is not None else getattr(self.g.cfg, "feed_travel", None)
        if self._cur_xy is not None:
            self._partial_lift_to_travel(self._cur_xy, opts)
        else:
            self.g.pen_set(1.0, step=opts.z_step, step_delay_s=opts.z_step_delay, wait=False)
            if opts.settle_up_s > 0:
                time.sleep(opts.settle_up_s)
        self.g.move_xy(x, y, feed=feed, wait=True)
        self._cur_xy = (x, y)

    def _travel_estimate(self, pattern: Pattern, start_xy: XY) -> float:
        cur = start_xy
        total = 0.0
        for it in pattern.items:
            s, e = it.endpoints()
            total += math.hypot(s[0] - cur[0], s[1] - cur[1])
            cur = e
        return total


__all__ = ["PlotRenderer", "RenderOptions", "DEFAULT_PEN_COLORS"]
