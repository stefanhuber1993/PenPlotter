"""Geometry primitives and pattern utilities for PenPlotter.

The original project mixed geometry definitions, optimisation helpers and the
renderer inside a single `pattern.py` file that was mostly used from a notebook.
This module extracts the pure data structures so they can be re-used by scripts,
command line utilities or the web server without importing any UI related code.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional, Tuple, Union, Dict, Any
import math
import copy

XY = Tuple[float, float]


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


@dataclass
class Line:
    """Simple two point line segment.

    Lines are transparently converted to :class:`Polyline` instances when they
    are added to a :class:`Pattern`.  The class mainly exists to provide a
    friendly API when creating shapes from scripts.
    """

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
    """Ordered set of points."""

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

    def clone(self) -> "Polyline":
        return copy.deepcopy(self)


@dataclass
class Circle:
    """Implicit circle definition that is polygonised on insertion."""

    c: XY
    r: float
    start_deg: float = 0.0
    sweep_deg: float = 360.0
    pen_pressure: float = -0.1
    seg_len_mm: float = 0.3  # chord length when polygonizing
    name: str = "circle"
    feed_draw: Optional[int] = None
    pen_id: int = 0
    _rev: bool = False

    def _point_at(self, ang_deg: float) -> XY:
        a = math.radians(ang_deg)
        return (self.c[0] + self.r * math.cos(a), self.c[1] + self.r * math.sin(a))

    def to_polyline(self) -> Polyline:
        """Polygonise the circle into a polyline."""
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


# ---------------------------------------------------------------------------
# Pattern container
# ---------------------------------------------------------------------------


@dataclass
class Pattern:
    """Collection of drawable primitives."""

    items: List[Polyline] = field(default_factory=list)

    # ---------------------------- creation helpers ----------------------------
    def add(self, *objs: Item) -> "Pattern":
        """Add primitives to the pattern.

        ``Line`` and ``Circle`` instances are converted to :class:`Polyline`
        objects immediately so the pattern only contains a single primitive type
        internally.  Returning ``self`` enables fluent chaining when building an
        art piece.
        """

        for obj in objs:
            if isinstance(obj, Line):
                s, e = obj.endpoints()
                self.items.append(
                    Polyline(
                        [s, e],
                        pen_pressure=obj.pen_pressure,
                        feed_draw=obj.feed_draw,
                        pen_id=obj.pen_id,
                    )
                )
            elif isinstance(obj, Circle):
                self.items.append(obj.to_polyline())
            elif isinstance(obj, Polyline):
                self.items.append(obj)
            else:
                raise TypeError(f"Unsupported object: {type(obj)!r}")
        return self

    def extend(self, items: Iterable[Item]) -> "Pattern":
        for obj in items:
            self.add(obj)
        return self

    # ----------------------------- high level info ---------------------------
    def clone(self) -> "Pattern":
        return Pattern(items=[it.clone() for it in self.items])

    def bounding_box(self) -> Optional[Tuple[XY, XY]]:
        xs: List[float] = []
        ys: List[float] = []
        for it in self.items:
            for x, y in it.pts:
                xs.append(x)
                ys.append(y)
        if not xs or not ys:
            return None
        return (min(xs), min(ys)), (max(xs), max(ys))

    def total_length(self) -> float:
        total = 0.0
        for it in self.items:
            pts = it.pts
            for a, b in zip(pts, pts[1:]):
                total += math.hypot(a[0] - b[0], a[1] - b[1])
        return total

    def __iter__(self) -> Iterator[Polyline]:
        return iter(self.items)

    # --------------------------- post processing ----------------------------
    def optimize_order_nn(self, start_xy: XY = (0.0, 0.0)) -> None:
        remaining = list(self.items)
        ordered: List[Polyline] = []
        cur = start_xy

        def dist(a: XY, b: XY) -> float:
            return math.hypot(a[0] - b[0], a[1] - b[1])

        while remaining:
            best_i, best_cost, best_cfg = None, float("inf"), False
            for i, it in enumerate(remaining):
                s0, e0 = it.endpoints()
                d_fwd = dist(cur, s0)
                d_rev = dist(cur, e0)
                cost, cfg = (d_fwd, False) if d_fwd <= d_rev else (d_rev, True)
                if cost < best_cost:
                    best_i, best_cost, best_cfg = i, cost, cfg
            it = remaining.pop(best_i)  # type: ignore[arg-type]
            it._rev = bool(best_cfg)
            _, e = it.endpoints()
            cur = e
            ordered.append(it)
        self.items = ordered

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
        ratio = pts_after / max(1, pts_before)
        return (
            "Resample: {n} polylines, points {before} -> {after} (x{ratio:.2f}), "
            "max_dev={max_dev}, max_seg={max_seg}."
        ).format(
            n=n_poly,
            before=pts_before,
            after=pts_after,
            ratio=ratio,
            max_dev=max_dev_mm,
            max_seg=max_seg_mm,
        )

    def combine_endpoints(self, *, join_tol_mm: float = 0.05) -> str:
        """Merge chains that share endpoints within ``join_tol_mm``."""

        chains: List[Tuple[List[XY], float, Optional[int], int]] = []

        for it in self.items:
            pts = list(reversed(it.pts)) if it._rev else list(it.pts)
            if pts:
                chains.append((pts, it.pen_pressure, it.feed_draw, it.pen_id))

        merged: List[Tuple[List[XY], float, Optional[int], int]] = []
        used = [False] * len(chains)

        for i, chain in enumerate(chains):
            if used[i]:
                continue
            pts_i, press_i, fd_i, pen_i = chain
            used[i] = True
            changed = True
            while changed:
                changed = False
                tail = pts_i[-1]
                for j, other in enumerate(chains):
                    if used[j] or other[3] != pen_i:
                        continue
                    pts_j = other[0]
                    head = pts_j[0]
                    if math.hypot(tail[0] - head[0], tail[1] - head[1]) <= join_tol_mm:
                        pts_i.extend(pts_j[1:])
                        used[j] = True
                        changed = True
                        break
            merged.append((pts_i, press_i, fd_i, pen_i))

        lifts_before = max(0, len(self.items) - 1)
        self.items = [Polyline(pts=m[0], pen_pressure=m[1], feed_draw=m[2], pen_id=m[3]) for m in merged]
        lifts_after = max(0, len(self.items) - 1)
        saved = lifts_before - lifts_after
        return (
            "Combine endpoints: merged {merges} links, pen lifts {before} -> {after} "
            "(saved {saved}), join_tol={tol} mm."
        ).format(
            merges=len(merged),
            before=lifts_before,
            after=lifts_after,
            saved=saved,
            tol=join_tol_mm,
        )

    # ------------------------------- serialisation ---------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "items": [
                {
                    "type": "polyline",
                    "points": [[float(x), float(y)] for x, y in it.pts],
                    "pen_pressure": it.pen_pressure,
                    "name": it.name,
                    "feed_draw": it.feed_draw,
                    "pen_id": it.pen_id,
                }
                for it in self.items
            ]
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "Pattern":
        pat = Pattern()
        for item in data.get("items", []):
            if item.get("type") != "polyline":
                raise ValueError(f"Unsupported item type: {item.get('type')}")
            pts = [(float(x), float(y)) for x, y in item.get("points", [])]
            pat.add(
                Polyline(
                    pts=pts,
                    pen_pressure=float(item.get("pen_pressure", -0.1)),
                    name=item.get("name", "polyline"),
                    feed_draw=item.get("feed_draw"),
                    pen_id=int(item.get("pen_id", 0)),
                )
            )
        return pat

    def strokes(self) -> List[Dict[str, Any]]:
        """Return a list of strokes suitable for front-end rendering."""
        strokes: List[Dict[str, Any]] = []
        for it in self.items:
            pts = list(reversed(it.pts)) if it._rev else list(it.pts)
            if len(pts) < 2:
                continue
            strokes.append(
                {
                    "points": [[float(x), float(y)] for x, y in pts],
                    "pen": int(it.pen_id),
                    "pen_pressure": float(it.pen_pressure),
                    "feed_draw": it.feed_draw,
                }
            )
        return strokes


# ---------------------------------------------------------------------------
# Geometry utilities
# ---------------------------------------------------------------------------


def _rdp(pts: List[XY], eps: float) -> List[XY]:
    if len(pts) <= 2:
        return pts[:]
    stack = [(0, len(pts) - 1)]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    while stack:
        i0, i1 = stack.pop()
        a, b = pts[i0], pts[i1]
        max_d = -1.0
        idx = None
        for i in range(i0 + 1, i1):
            ax, ay = a
            bx, by = b
            px, py = pts[i]
            dx, dy = bx - ax, by - ay
            if dx == 0 and dy == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
                t = max(0.0, min(1.0, t))
                cx, cy = ax + t * dx, ay + t * dy
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
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L <= max_seg or max_seg <= 0:
            out.append(b)
            continue
        n = max(1, int(math.ceil(L / max_seg)))
        for k in range(1, n + 1):
            t = k / n
            out.append((ax + t * dx, ay + t * dy))
    return out


def _resample_polyline_pts(
    pts: List[XY], *, max_dev_mm: Optional[float], max_seg_mm: Optional[float]
) -> List[XY]:
    out = pts
    if max_dev_mm and max_dev_mm > 0:
        out = _rdp(out, max_dev_mm)
    if max_seg_mm and max_seg_mm > 0:
        out = _split_long(out, max_seg_mm)
    return out


__all__ = [
    "XY",
    "Line",
    "Polyline",
    "Circle",
    "Pattern",
]
