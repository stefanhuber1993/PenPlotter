# pattern.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Tuple, Optional, Union, Iterable
import math, time, threading

XY = Tuple[float, float]

# ----------------------------- Primitives --------------------------------
# Line is deprecated. It will be converted to a 2-point Polyline in Pattern.add.

@dataclass
class Line:
    p0: XY
    p1: XY
    pen_pressure: float = -0.1
    name: str = "line"
    feed_draw: Optional[int] = None
    pen_id: int = 0
    _rev: bool = False

    def endpoints(self) -> Tuple[XY, XY]:
        return (self.p1, self.p0) if self._rev else (self.p0, self.p1)


@dataclass
class Polyline:
    pts: List[XY]
    pen_pressure: float = -0.1
    name: str = "polyline"
    feed_draw: Optional[int] = None  # mm/min. If None, device cfg.feed_draw is used.
    pen_id: int = 0
    _rev: bool = False

    def endpoints(self) -> Tuple[XY, XY]:
        if not self.pts:
            return (0.0, 0.0), (0.0, 0.0)
        if self._rev:
            return (self.pts[-1], self.pts[0])
        return (self.pts[0], self.pts[-1])


@dataclass
class Circle:
    c: XY
    r: float
    start_deg: float = 0.0
    sweep_deg: float = 360.0
    pen_pressure: float = -0.1
    seg_len_mm: float = 0.3   # chord length when polygonizing
    name: str = "circle"
    feed_draw: Optional[int] = None
    pen_id: int = 0
    _rev: bool = False

    def _point_at(self, ang_deg: float) -> XY:
        a = math.radians(ang_deg)
        return (self.c[0] + self.r * math.cos(a), self.c[1] + self.r * math.sin(a))

    def to_polyline(self) -> Polyline:
        # Polygonize once on add. After this, everything is Polyline.
        arc_len = abs(math.radians(self.sweep_deg)) * self.r
        n = max(3, int(math.ceil(arc_len / max(1e-9, self.seg_len_mm))))
        s = self.start_deg
        eang = self.start_deg + self.sweep_deg
        if self._rev:
            s, eang = eang, s
        pts: List[XY] = []
        for k in range(n + 1):
            t = k / n
            ang = s + (eang - s) * t
            pts.append(self._point_at(ang))
        return Polyline(pts=pts, pen_pressure=self.pen_pressure, feed_draw=self.feed_draw, pen_id=self.pen_id)


Item = Union[Line, Polyline, Circle]

# ----------------------------- Pattern -----------------------------------

@dataclass
class Pattern:
    items: List[Item] = field(default_factory=list)

    def add(self, *objs: Item):
        # Convert Line -> Polyline and Circle -> Polyline immediately.
        for obj in objs:
            if isinstance(obj, Line):
                s, e = obj.endpoints()
                self.items.append(Polyline([s, e],
                                           pen_pressure=obj.pen_pressure,
                                           feed_draw=obj.feed_draw,
                                           pen_id=obj.pen_id))
            elif isinstance(obj, Circle):
                self.items.append(obj.to_polyline())
            else:
                self.items.append(obj)
        return self

    def preview(self) -> str:
        lines = []
        for it in self.items:
            if isinstance(it, Polyline):
                s, e = it.endpoints()
                lines.append(f"POLYLINE n={len(it.pts)} {s}->{e} pen_pressure={it.pen_pressure} pen={it.pen_id}")
            else:
                lines.append(f"UNEXPECTED {type(it).__name__}")
        return "\n".join(lines)

    def optimize_order_nn(self, start_xy: XY = (0.0, 0.0)) -> None:
        remaining = list(self.items)
        ordered: List[Item] = []
        cur = start_xy

        def dist(a: XY, b: XY) -> float:
            return math.hypot(a[0]-b[0], a[1]-b[1])

        while remaining:
            best_i, best_cost, best_cfg = None, float("inf"), False
            for i, it in enumerate(remaining):
                s0, e0 = it.endpoints()
                d_fwd = dist(cur, s0)
                d_rev = dist(cur, e0)
                cost, cfg = (d_fwd, False) if d_fwd <= d_rev else (d_rev, True)
                if cost < best_cost:
                    best_i, best_cost, best_cfg = i, cost, cfg
            it = remaining.pop(best_i)  # type: ignore
            it._rev = bool(best_cfg)
            _, e = it.endpoints()
            cur = e
            ordered.append(it)
        self.items = ordered

    # -------- Pattern-level resampling and combining --------

    def resample_polylines(self, *, max_dev_mm: Optional[float], max_seg_mm: Optional[float]) -> str:
        n_poly, pts_before, pts_after = 0, 0, 0
        for it in self.items:
            if isinstance(it, Polyline):
                n_poly += 1
                pts_before += len(it.pts)
                it.pts = _resample_polyline_pts(it.pts, max_dev_mm=max_dev_mm, max_seg_mm=max_seg_mm)
                pts_after += len(it.pts)
        if n_poly == 0:
            return "Resample: no polylines."
        ratio = (pts_after / max(1, pts_before))
        return f"Resample: {n_poly} polylines, points {pts_before} -> {pts_after} (x{ratio:.2f}), max_dev={max_dev_mm}, max_seg={max_seg_mm}."

    def combine_endpoints(self, *, join_tol_mm: float = 0.05) -> str:
        """
        Merge by endpoint proximity only. Chains may flip direction to match.
        Only chains with the same pen_id are merged.
        """
        # (pts, pen_pressure, feed_draw, pen_id)
        chains: List[Tuple[List[XY], float, Optional[int], int]] = []

        for it in self.items:
            if isinstance(it, Polyline):
                pts = list(reversed(it.pts)) if it._rev else list(it.pts)
                if pts:
                    chains.append((pts, it.pen_pressure, it.feed_draw, it.pen_id))

        used = [False] * len(chains)
        merged: List[Tuple[List[XY], float, Optional[int], int]] = []
        merges_done = 0

        def almost(a: XY, b: XY) -> bool:
            return math.hypot(a[0]-b[0], a[1]-b[1]) <= join_tol_mm

        for i in range(len(chains)):
            if used[i]:
                continue
            pts_i, press_i, fd_i, pen_i = chains[i]
            if not pts_i:
                used[i] = True
                continue

            chain = pts_i[:]
            used[i] = True

            changed = True
            while changed:
                changed = False
                for j in range(len(chains)):
                    if used[j] or j == i:
                        continue
                    pts_j, press_j, fd_j, pen_j = chains[j]
                    if not pts_j or pen_j != pen_i:
                        continue

                    # four endpoint match cases
                    if almost(chain[-1], pts_j[0]):
                        chain = chain + pts_j[1:]
                    elif almost(chain[-1], pts_j[-1]):
                        chain = chain + list(reversed(pts_j[:-1]))
                    elif almost(chain[0], pts_j[-1]):
                        chain = pts_j[:-1] + chain
                    elif almost(chain[0], pts_j[0]):
                        chain = list(reversed(pts_j[1:])) + chain
                    else:
                        continue

                    used[j] = True
                    merges_done += 1
                    changed = True

            merged.append((chain, press_i, fd_i, pen_i))

        lifts_before = max(0, len(self.items) - 1)
        self.items = [Polyline(pts=m[0], pen_pressure=m[1], feed_draw=m[2], pen_id=m[3]) for m in merged]
        lifts_after = max(0, len(self.items) - 1)
        saved = lifts_before - lifts_after
        return f"Combine endpoints: merged {merges_done} links, pen lifts {lifts_before} -> {lifts_after} (saved {saved}), join_tol={join_tol_mm} mm."

# ----------------------------- Geometry utils -----------------------------

def _rdp(pts: List[XY], eps: float) -> List[XY]:
    if len(pts) <= 2:
        return pts[:]
    stack = [(0, len(pts) - 1)]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    while stack:
        i0, i1 = stack.pop()
        a, b = pts[i0], pts[i1]
        max_d = -1.0; idx = None
        for i in range(i0 + 1, i1):
            # perpendicular distance from pts[i] to segment ab
            ax, ay = a; bx, by = b; px, py = pts[i]
            dx, dy = bx - ax, by - ay
            if dx == 0 and dy == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / (dx*dx + dy*dy)
                t = max(0.0, min(1.0, t))
                cx, cy = ax + t*dx, ay + t*dy
                d = math.hypot(px - cx, py - cy)
            if d > max_d:
                max_d, idx = d, i
        if max_d > eps and idx is not None:
            keep[idx] = True
            stack.append((i0, idx))
            stack.append((idx, i1))
    return [p for p, k in zip(pts, keep) if k]

def _split_long(pts: List[XY], max_seg: float) -> List[XY]:
    if not pts:
        return pts
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        ax, ay = a; bx, by = b
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L <= max_seg or max_seg <= 0:
            out.append(b); continue
        n = max(1, int(math.ceil(L / max_seg)))
        for k in range(1, n + 1):
            t = k / n
            out.append((ax + t*dx, ay + t*dy))
    return out

def _resample_polyline_pts(pts: List[XY], *, max_dev_mm: Optional[float], max_seg_mm: Optional[float]) -> List[XY]:
    out = pts
    if max_dev_mm and max_dev_mm > 0:
        out = _rdp(out, max_dev_mm)
    if max_seg_mm and max_seg_mm > 0:
        out = _split_long(out, max_seg_mm)
    return out

# ------------------------------ Renderer ---------------------------------

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


class RendererCancelled(Exception):
    """Raised when a renderer run is cancelled."""


class Renderer:
    """
    Executes a Pattern on a GRBL-like device and can preview into the PPW widget.

    Renderer owns:
      z_mode: 'start' | 'centroid' | 'per_segment' | 'threshold'
      z_threshold, settle_down_s, settle_up_s, z_step, z_step_delay
      flush_every, feed_travel, lift_delta
    """
    def __init__(self, grbl, *,
                 z_mode: str = "per_segment",
                 z_threshold: float = 0.02,
                 settle_down_s: float = 0.05,
                 settle_up_s: float = 0.03,
                 z_step: Optional[float] = None,
                 z_step_delay: float = 0.03,
                 flush_every: int = 200,
                 feed_travel: Optional[int] = None,
                 lift_delta: float = 0.2,
                 pen_colors: Optional[dict] = None,
                 widget_api: Optional[dict] = None):
        self.g = grbl
        self.z_mode = z_mode
        self.z_threshold = float(z_threshold)
        self.settle_down_s = float(settle_down_s)
        self.settle_up_s = float(settle_up_s)
        self.z_step = z_step
        self.z_step_delay = float(z_step_delay)
        self.flush_every = int(max(1, flush_every))
        self.feed_travel = feed_travel
        self.lift_delta = float(max(0.0, lift_delta))
        self._cur_xy: Optional[XY] = None
        self.pen_colors = dict(DEFAULT_PEN_COLORS)
        if pen_colors:
            self.pen_colors.update(pen_colors)
        self.widget_api = widget_api  # optional explicit attach
        self._cancel_requested = False
        self._pause_event = threading.Event()
        self._pause_event.set()

    # ------------------------------ Public API ----------------------------

    def attach_widget_api(self, api: dict) -> None:
        """Manually attach the PPW widget API if you have it."""
        self.widget_api = api

    def _resolve_widget_api(self):
        # 1) explicit attachment wins
        if isinstance(self.widget_api, dict):
            return self.widget_api
        # 2) notebook global
        api = globals().get("_PPW_API")
        if isinstance(api, dict):
            self.widget_api = api
            return api
        # 3) from module
        try:
            import penplot_widgets as ppw
            api = getattr(ppw, "_PPW_API", None)
            if isinstance(api, dict):
                self.widget_api = api
                return api
        except Exception:
            pass
        return None

    def reset_control(self) -> None:
        self._cancel_requested = False
        self._pause_event.set()

    def request_cancel(self) -> None:
        self._cancel_requested = True
        self._pause_event.set()
        try:
            if getattr(self.g, 'ser', None):
                self.g.ser.write(b'!')
                self.g.ser.flush()
        except Exception:
            pass

    def request_pause(self) -> None:
        self._pause_event.clear()
        try:
            if getattr(self.g, 'ser', None):
                self.g.ser.write(b'!')
                self.g.ser.flush()
        except Exception:
            pass

    def request_resume(self) -> None:
        if not self._pause_event.is_set():
            self._pause_event.set()
            try:
                if getattr(self.g, 'ser', None):
                    self.g.ser.write(b'~')
                    self.g.ser.flush()
            except Exception:
                pass

    def _wait_if_paused(self) -> None:
        self._pause_event.wait()

    def _check_cancelled(self) -> None:
        if self._cancel_requested:
            raise RendererCancelled()

    def plot(self, pattern: Pattern, *, pens: Optional[Union[int, Iterable[int]]] = None,
             width: float = 1.5) -> None:
        """
        Send the current pattern preview to the PPW widget overlay.
        pens: None (all) or int or iterable of ints to filter.
        """
        try:
            api = self._resolve_widget_api()
        except Exception:
            api = None
        if api and "ingest_pattern" in api:
            api["ingest_pattern"](pattern, {
                "z_mode": self.z_mode,
                "z_threshold": self.z_threshold,
                "settle_down_s": self.settle_down_s,
                "settle_up_s": self.settle_up_s,
                "z_step": self.z_step,
                "z_step_delay": self.z_step_delay,
                "flush_every": self.flush_every,
                "feed_travel": self.feed_travel,
                "lift_delta": self.lift_delta,
            })

        api = self._resolve_widget_api()

        if not api or "plot_replace" not in api:
            print("PPW widget API not available. Run show_area_and_compensation_widget(grbl) first, "
                  "or attach via r.attach_widget_api(ppw._PPW_API).")
            return

        pens_set = None
        if pens is not None:
            pens_set = set([pens] if isinstance(pens, int) else list(pens))

        strokes = []
        for it in pattern.items:
            if isinstance(it, Polyline):
                if pens_set is not None and it.pen_id not in pens_set:
                    continue
                pts = list(reversed(it.pts)) if it._rev else list(it.pts)
                if len(pts) < 2:
                    continue
                color = self.pen_colors.get(it.pen_id, DEFAULT_PEN_COLORS[0])
                strokes.append({
                    "pts": pts,
                    "color": color,
                    "width": width,
                    "pen": f"pen{it.pen_id}",
                })

        api["plot_replace"](strokes)

    # ------------------------------ Execution -----------------------------

    def run(self, pattern: Pattern, *,
            start_xy: XY = (0.0, 0.0),
            optimize: Optional[str] = None,    # 'nn' or None
            resample: Optional[dict] = None,   # {'max_dev_mm':..., 'max_seg_mm':...}
            combine: Optional[dict] = None,    # {'join_tol_mm':...}
            return_home: bool = True,
            pen_filter: Optional[Union[int, Iterable[int]]] = None,
            preview_in_widget: bool = False) -> None:
        """
        pen_filter: None (all pens) or an int / iterable of ints to restrict draw.
        preview_in_widget: if True, send a preview to the widget before executing.
        """

        self.reset_control()

        if combine is not None:
            msg = pattern.combine_endpoints(join_tol_mm=combine.get('join_tol_mm', 0.05))
            print(msg)

        if resample is not None:
            msg = pattern.resample_polylines(max_dev_mm=resample.get('max_dev_mm', None),
                                             max_seg_mm=resample.get('max_seg_mm', None))
            print(msg)

        if optimize == 'nn':
            before = self._travel_estimate(pattern, start_xy)
            pattern.optimize_order_nn(start_xy=start_xy)
            after = self._travel_estimate(pattern, start_xy)
            gain = max(0.0, before - after)
            pct = (gain / before * 100.0) if before > 0 else 0.0
            print(f"Optimize order: nn, travel {before:.2f} -> {after:.2f} mm, saved {gain:.2f} mm ({pct:.1f} percent).")

        if preview_in_widget:
            self.plot(pattern, pens=pen_filter)

        pens_set = None
        if pen_filter is not None:
            pens_set = set([pen_filter] if isinstance(pen_filter, int) else list(pen_filter))

        exec_items: List[Polyline] = []
        for it in pattern.items:
            if isinstance(it, Polyline):
                if pens_set is None or it.pen_id in pens_set:
                    exec_items.append(it)
            else:
                raise TypeError(f"Unsupported item at execution: {type(it).__name__}")

        self._cur_xy = start_xy

        for it in exec_items:
            self._check_cancelled()
            self._wait_if_paused()
            self._run_polyline(it)

        if return_home:
            self._check_cancelled()
            self._wait_if_paused()
            self._pen_set(1.0, settle=False)
            if self.settle_up_s > 0:
                time.sleep(self.settle_up_s)
            self._travel_to(0.0, 0.0)
            self._cur_xy = (0.0, 0.0)

    # ------------------------------ runners ------------------------------

    def _run_polyline(self, it: Polyline):
        if len(it.pts) < 2:
            return
        pts = list(reversed(it.pts)) if it._rev else list(it.pts)

        # travel to start at feed_travel
        x0, y0 = pts[0]
        self._check_cancelled()
        self._wait_if_paused()
        self._travel_to(x0, y0)

        # per-shape draw feed
        if it.feed_draw is not None and hasattr(self.g, "cfg"):
            try:
                self.g.cfg.feed_draw = int(it.feed_draw)
            except Exception:
                pass

        # choose z per renderer mode
        if self.z_mode in ("start", "centroid"):
            if self.z_mode == "start":
                zx, zy = pts[0]
            else:
                zx = sum(p[0] for p in pts) / len(pts)
                zy = sum(p[1] for p in pts) / len(pts)
            z = self._pen_pos(zx, zy, it.pen_pressure)
            self._pen_set(z, settle=True)
            for i in range(1, len(pts)):
                self._check_cancelled()
                self._wait_if_paused()
                ex, ey = pts[i]
                self.g.draw_xy(ex, ey, wait=False)
                if i % self.flush_every == 0:
                    self.g.wait_idle()

        elif self.z_mode == "per_segment":
            sx, sy = pts[0]
            ex, ey = pts[1]
            mx, my = 0.5*(sx+ex), 0.5*(sy+ey)
            z = self._pen_pos(mx, my, it.pen_pressure)
            self._pen_set(z, settle=True)
            for i in range(1, len(pts)):
                sx, sy = pts[i - 1]; ex, ey = pts[i]
                mx, my = 0.5*(sx+ex), 0.5*(sy+ey)
                z = self._pen_pos(mx, my, it.pen_pressure)
                self._check_cancelled()
                self._wait_if_paused()
                self._pen_set(z, settle=False)
                self.g.draw_xy(ex, ey, wait=False)
                if i % self.flush_every == 0:
                    self.g.wait_idle()

        elif self.z_mode == "threshold":
            sx, sy = pts[0]
            ex, ey = pts[1]
            mx, my = 0.5*(sx+ex), 0.5*(sy+ey)
            cur_z = self._pen_pos(mx, my, it.pen_pressure)
            self._pen_set(cur_z, settle=True)
            for i in range(1, len(pts)):
                sx, sy = pts[i - 1]; ex, ey = pts[i]
                mx, my = 0.5*(sx+ex), 0.5*(sy+ey)
                z = self._pen_pos(mx, my, it.pen_pressure)
                self._check_cancelled()
                self._wait_if_paused()
                if abs(z - cur_z) > self.z_threshold:
                    self._pen_set(z, settle=False)
                    cur_z = z
                self.g.draw_xy(ex, ey, wait=False)
                if i % self.flush_every == 0:
                    self.g.wait_idle()
        else:
            raise ValueError(f"Unknown z_mode: {self.z_mode}")

        self.g.wait_idle()
        self._partial_lift_to_travel(pts[-1])
        self._cur_xy = pts[-1]

    # ------------------------- internal helpers --------------------------

    def _pen_pos(self, x: float, y: float, pen_pressure: float) -> float:
        # grbl.compensated_pos expects 'pos_offset'; map our pen_pressure to it.
        return self.g.compensated_pos(x, y, pos_offset=pen_pressure)

    def _pen_set(self, target: float, *, settle: bool):
        self._check_cancelled()
        self._wait_if_paused()
        self.g.pen_set(target, step=self.z_step, step_delay_s=self.z_step_delay, wait=False)
        if settle and self.settle_down_s > 0:
            time.sleep(self.settle_down_s)

    def _partial_lift_to_travel(self, xy: XY):
        x, y = xy
        base = self._pen_pos(x, y, 0.0)
        target = min(1.0, base + self.lift_delta)
        self._check_cancelled()
        self._wait_if_paused()
        self.g.pen_set(target, step=self.z_step, step_delay_s=self.z_step_delay, wait=False)
        if self.settle_up_s > 0:
            time.sleep(self.settle_up_s)

    def _travel_to(self, x: float, y: float):
        self._check_cancelled()
        self._wait_if_paused()
        feed = self.feed_travel if self.feed_travel is not None else getattr(self.g.cfg, "feed_travel", None)
        if self._cur_xy is not None:
            self._partial_lift_to_travel(self._cur_xy)
        else:
            self.g.pen_set(1.0, step=self.z_step, step_delay_s=self.z_step_delay, wait=False)
            if self.settle_up_s > 0:
                time.sleep(self.settle_up_s)
        self.g.move_xy(x, y, feed=feed, wait=True)
        self._cur_xy = (x, y)

    def _travel_estimate(self, pattern: Pattern, start_xy: XY) -> float:
        cur = start_xy
        total = 0.0
        for it in pattern.items:
            s, e = it.endpoints()
            total += math.hypot(s[0]-cur[0], s[1]-cur[1])
            cur = e
        return total
