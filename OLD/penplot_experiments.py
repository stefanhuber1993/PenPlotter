# penplot_experiments.py
from __future__ import annotations
import math
from typing import Tuple, Optional, Sequence

from pattern import Pattern, Polyline, Renderer, Circle, plot_plan  # your utilities

XY   = Tuple[float, float]
Rect = Tuple[float, float, float, float]

# Internal utility functions
def _max_circle_through_point_exact(rect: Rect, P: XY, phi_deg: float, safety_mm: float = 0.6):
    """
    Largest circle fully inside axis-aligned rect that passes through P,
    with center on the ray from P at angle phi.
    Closed-form via edge constraints; no iteration.
    Returns (Cx, Cy, r). If infeasible: r=0.
    """
    (x0, y0, x1, y1) = rect
    (Px, Py) = P
    phi = math.radians(phi_deg)
    c, s = math.cos(phi), math.sin(phi)

    # Distances from P to each edge
    L = Px - x0; R = x1 - Px; B = Py - y0; T = y1 - Py

    def _rb(num, denom):
        return (num / denom) if denom > 1e-12 else float("inf")

    r1 = _rb(L, 1.0 - c)   # left
    r2 = _rb(R, 1.0 + c)   # right
    r3 = _rb(B, 1.0 - s)   # bottom
    r4 = _rb(T, 1.0 + s)   # top

    r = max(0.0, min(r1, r2, r3, r4) - safety_mm)
    if r <= 0.0:
        return Px, Py, 0.0

    Cx, Cy = Px + r * c, Py + r * s
    return Cx, Cy, r




# List of Experiments 

def ex1_circles_through_pile(cfg,
                         grbl,
                         rect: Rect,
                         pots: Sequence, *,
                         n_angles: int = 36,
                         start_lead_deg: float = 10.0,
                         pos_off: float = -0.20,
                         settle_s: float = 0.10,
                         feed_draw: int = 3000,
                         safety_mm: float = 0.6,
                         pile_xy: Optional[XY] = None,
                         plot: bool = True,
                         execute: bool = False,
                         figure_kwargs: Optional[dict] = None) -> Pattern:
    """
    Build a pattern of maximal circles that all pass through a central paint pile.

    Args
    ----
    cfg, grbl : your device objects (cfg used only for plotting extents).
    rect      : (x0,y0,x1,y1) calibrated area.
    pots      : sequence of pot objects (for plotting only).
    n_angles  : number of directions sampled around the pile.
    start_lead_deg : start angle offset so each circle enters the pile ~10° earlier.
    pos_off   : additive pen pos bias (negative pushes down further).
    settle_s  : dwell after pen_set before motion (s).
    feed_draw : drawing feed (mm/min).
    safety_mm : clearance from rectangle edges.
    pile_xy   : (x,y) of pile; default is rect center.
    plot      : if True, render a preview via plot_plan.
    execute   : if True, send to plotter via Renderer(grbl).run(...).
    figure_kwargs : passed to plot_plan (e.g., figsize).

    Returns
    -------
    Pattern
    """
    x0, y0, x1, y1 = rect
    if pile_xy is None:
        Px, Py = 0.5 * (x0 + x1), 0.5 * (y0 + y1)
    else:
        Px, Py = float(pile_xy[0]), float(pile_xy[1])

    pat = Pattern()
    for k in range(max(1, int(n_angles))):
        phi = k * (360.0 / n_angles)
        Cx, Cy, r = _max_circle_through_point_exact(rect, (Px, Py), phi, safety_mm=safety_mm)
        if r < 1.0:
            continue
        # Angle from center to pile; begin slightly before to pass through pile
        alpha = math.atan2(Py - Cy, Px - Cx)
        start_angle = alpha - math.radians(start_lead_deg)

        c = Circle(center=(Cx, Cy), radius=r,
                   pos_offset=pos_off, settle_s=settle_s,
                   ccw=True, feed_draw=feed_draw)
        # Renderer respects _start_angle if set
        c._start_angle = start_angle
        pat.add(c)

    if plot:
        fk = dict(step=50.0, figsize=(9.2, 6.0), title="Maximal circles through center pile")
        if figure_kwargs:
            fk.update(figure_kwargs)
        plot_plan((cfg.x_max, cfg.y_max), rect, pots, pat, **fk)

    if execute:
        Renderer(grbl).run(pat)

    return pat



__all__ = ["circles_through_pile"]



def _clip_pt_to_rect(pt: XY, rect: Rect, margin: float) -> XY:
    x0, y0, x1, y1 = rect
    x = min(x1 - margin, max(x0 + margin, pt[0]))
    y = min(y1 - margin, max(y0 + margin, pt[1]))
    return (x, y)

def _max_isosceles_triangle(rect: Rect, P: XY, phi_deg: float, half_apex_deg: float, safety_mm: float):
    """
    Largest **isosceles** triangle with apex at P, symmetry axis along +phi,
    base centered on that axis, that fits inside `rect` with margin `safety_mm`.

    Triangle parameterization:
      apex A = P
      altitude a ≥ 0 along u = (cos phi, sin phi)
      base half-width w = a * tan(half_apex)
      base vertices:
        B1 = P + a*u + w*v,   B2 = P + a*u - w*v
        with v = (-sin phi, cos phi) (left-normal)

    We find the maximum feasible `a` such that B1 and B2 lie inside the rect.
    Returns (A, B1, B2). If infeasible: returns (P, P, P) with zero size.
    """
    (x0, y0, x1, y1) = rect
    Px, Py = P
    phi = math.radians(phi_deg)
    t = math.tan(math.radians(half_apex_deg))
    c, s = math.cos(phi), math.sin(phi)

    # Linear coefficients for B1 and B2:
    # B1 = P + a*(c - t*s,  s + t*c)
    # B2 = P + a*(c + t*s,  s - t*c)
    k1x, k1y = (c - t*s), (s + t*c)
    k2x, k2y = (c + t*s), (s - t*c)

    # Bounds (shift by P and margin)
    xl, xr = (x0 + safety_mm - Px), (x1 - safety_mm - Px)
    yb, yt = (y0 + safety_mm - Py), (y1 - safety_mm - Py)

    # Helper: range of 'a' for low ≤ Px + a*k ≤ high
    def range_for(k, low, high):
        if abs(k) < 1e-12:
            # No dependence on a: feasible only if Px in [low,high]
            return (0.0, float("inf")) if (low <= 0.0 <= high) else (1.0, 0.0)  # empty
        lo, hi = (low / k, high / k)
        return (min(lo, hi), max(lo, hi))

    # Intersect ranges from x/y constraints of both base vertices
    rngs = [
        range_for(k1x, xl, xr),
        range_for(k1y, yb, yt),
        range_for(k2x, xl, xr),
        range_for(k2y, yb, yt),
    ]
    a_lo = max(r[0] for r in rngs + [(0.0, float("inf"))])  # also enforce a>=0
    a_hi = min(r[1] for r in rngs)
    if a_hi < a_lo + 1e-9:
        return (P, P, P)  # infeasible

    a = a_hi
    w = a * t
    # Unit vectors
    u = (c, s)
    v = (-s, c)

    A = (Px, Py)
    B1 = (Px + a*u[0] + w*v[0], Py + a*u[1] + w*v[1])
    B2 = (Px + a*u[0] - w*v[0], Py + a*u[1] - w*v[1])
    return A, B1, B2


def ex2_triangles_from_pile(cfg,
                        grbl,
                        rect: Rect,
                        pots: Sequence, *,
                        n_angles: int = 36,
                        half_apex_deg: float = 18.0,   # sharp tip; increase for wider triangles
                        lead_mm: float = 2.0,          # start this far **before** the apex along first edge
                        pos_off: float = -0.18,
                        settle_s: float = 0.10,
                        feed_draw: int = 9000,
                        safety_mm: float = 0.6,
                        pile_xy: Optional[XY] = None,
                        plot: bool = True,
                        execute: bool = False,
                        figure_kwargs: Optional[dict] = None) -> Pattern:
    """
    Maximal isosceles triangles with their **tip at the paint pile**, base expanded to fill the area.
    Each triangle perimeter path starts a little *before* the apex to scoop pigment, then goes:
        S (lead) → Apex → B1 → B2 → Apex

    Args mirror circles_through_pile where sensible.
    """
    x0, y0, x1, y1 = rect
    if pile_xy is None:
        Px, Py = 0.5*(x0+x1), 0.5*(y0+y1)
    else:
        Px, Py = float(pile_xy[0]), float(pile_xy[1])

    pat = Pattern()

    for k in range(max(1, int(n_angles))):
        phi = k * (360.0 / n_angles)  # triangle axis direction (apex → base center)

        A, B1, B2 = _max_isosceles_triangle(rect, (Px, Py), phi,
                                            half_apex_deg=half_apex_deg,
                                            safety_mm=safety_mm)
        # Skip degenerate triangles
        if A == B1 == B2:
            continue

        # Choose the first edge to traverse from the apex: A→B1
        e0x, e0y = (B1[0] - A[0], B1[1] - A[1])
        L0 = math.hypot(e0x, e0y)
        if L0 < 1e-6:
            continue
        ux, uy = (e0x / L0, e0y / L0)
        S = (A[0] - lead_mm*ux, A[1] - lead_mm*uy)
        S = _clip_pt_to_rect(S, rect, safety_mm)  # keep start safely inside

        # Perimeter path (midpoint Z-comp for smoothness)
        pts = [S, A, B1, B2, A]
        pat.add(Polyline(
            pts=pts,
            z_mode="threshold",
            z_threshold=0.2,
            pos_offset=pos_off,
            settle_s=settle_s,
            min_seg_mm=1.0,     # keep segments reasonable
            max_seg_mm=250.0,
            feed_draw=feed_draw
        ))

    if plot:
        fk = dict(step=50.0, figsize=(9.2, 6.0), title="Maximal triangles from center pile")
        if figure_kwargs:
            fk.update(figure_kwargs)
        plot_plan((cfg.x_max, cfg.y_max), rect, [], pat, **fk)

    if execute:
        Renderer(grbl).run(pat)

    return pat


# Exports
try:
    __all__  # may already exist (e.g., circles_through_pile defined above)
except NameError:
    __all__ = []
__all__ += ["triangles_from_pile"]













# --- Curl-Noise Push for MULTIPLE pots
#     • per-pot agent counts (int OR list[int])
#     • repulsion from other pots (to avoid hitting them)
#     • per-pot stroke colors in preview (sets _plot_color on each Polyline)
#     • optional ordering of strokes by average direction from the pot (default ON)
#     • optional progressive tracing 0→p% (per agent) to transport paint
import math, random
from typing import Tuple, Optional, Sequence, List, Union
from pattern import Pattern, Renderer, Polyline, plot_plan

XY   = Tuple[float, float]
Rect = Tuple[float, float, float, float]

def ex4c_curl_push_multi(cfg,
                         grbl,
                         rect: Optional[Rect] = None,
                         pots: Optional[Sequence] = None, *,
                         pot_indices: Optional[Sequence[int]] = None,   # None → all pots in widget order
                         # per-pot budget: int (same for all) OR list/tuple[int] (one per pot)
                         agents_per_pot: Union[int, Sequence[int]] = 70,
                         steps: int = 150,
                         step_mm: float = 9.0,
                         # shared curl field (coherent look across pots)
                         noise_scale: float = 100.0,
                         curl_strength: float = 0.9,
                         # outward drift (from CURRENT pot)
                         drift_gain: float = 0.25,
                         # repulsion from OTHER pots
                         repulse_radius_mm: float = 24.0,
                         repulse_gain: float = 2.0,
                         # shaping
                         jitter_deg: float = 1.0,
                         boundary_safety: float = 1.2,
                         stop_at_boundary: bool = True,
                         # pass through pot center first
                         lead_in_mm: float = 7.0,
                         lead_out_mm: float = 11.0,
                         # motion / Z
                         z_mode: str = "threshold",
                         z_threshold: float = 0.2,
                         pos_off: float = -0.18,
                         settle_s: float = 0.12,
                         feed_draw: int = 9000,
                         min_seg_mm: float = 9.0,
                         max_seg_mm: float = 1e9,
                         # progressive tracing per agent (0→p% in increments)
                         progressive: bool = False,
                         progressive_increments: int = 10,
                         progressive_reverse: bool = True,
                         # ordering (for nicer optics)
                         order_by_direction: bool = True,  # sort strokes by average direction from pot
                         # output
                         plot: bool = True,
                         execute: bool = False,
                         random_ini: float = 42.0, 
                         figure_kwargs: Optional[dict] = None) -> Pattern:
    """Multi-pot curl-noise push with per-pot agent counts, per-pot colors, repulsion, and optional ordering."""

    # ---- autoload rect + pots from widget cache if missing ----
    def _autoload_rect_pots(_cfg):
        try:
            import penplot_widgets as ppw
            key = (round(_cfg.x_max, 3), round(_cfg.y_max, 3))
            s = getattr(ppw, "_PPW_STATE", {}).get(key)
            if not s: return None, None
            BL, TR = s["corners"]["BL"], s["corners"]["TR"]
            _rect = (float(BL["x"]), float(BL["y"]), float(TR["x"]), float(TR["y"]))
            _pots = s.get("pots", [])
            return _rect, _pots
        except Exception:
            return None, None

    def _pot_xy(p) -> XY:
        return (float(p.x), float(p.y)) if hasattr(p, "x") else (float(p["x"]), float(p["y"]))

    def _normalize_pots(seq):
        norm = []
        for p in (seq or []):
            if hasattr(p, "x"): norm.append(p); continue
            d = dict(p)
            class _PotLike:
                __slots__ = ("x","y","h","color","id")
                def __init__(self, d):
                    self.x = float(d.get("x", 0.0))
                    self.y = float(d.get("y", 0.0))
                    self.h = float(d.get("h", 1.0))
                    self.color = d.get("color", "#3a86ff")
                    self.id = d.get("id", None)
            norm.append(_PotLike(d))
        return norm

    if rect is None or pots is None:
        r_auto, p_auto = _autoload_rect_pots(cfg)
        if rect is None: rect = r_auto
        if pots is None: pots = p_auto
    if rect is None: raise ValueError("ex4c_curl_push_multi: rectangle not found (open the widget and save).")
    if not pots: raise ValueError("ex4c_curl_push_multi: need at least one pot in the widget cache.")

    pots_norm = _normalize_pots(pots)
    if pot_indices is None:
        pot_indices = list(range(len(pots_norm)))
    pot_indices = [i for i in pot_indices if 0 <= i < len(pots_norm)]
    if not pot_indices:
        raise ValueError("ex4c_curl_push_multi: pot_indices ended up empty.")

    # per-pot agent counts
    def _counts_for_pots(idx_list, spec):
        if isinstance(spec, int):
            return [int(spec)] * len(idx_list)
        seq = list(spec)
        if len(seq) >= len(idx_list):
            return [int(v) for v in seq[:len(idx_list)]]
        pad = seq[-1] if seq else 0
        return [int(v) for v in seq] + [int(pad)] * (len(idx_list) - len(seq))

    counts = _counts_for_pots(pot_indices, agents_per_pot)

    x0, y0, x1, y1 = rect

    # --- inset rectangle, inside test, and robust clipping (Liang–Barsky) ---
    def _inset_rect():
        return (x0 + boundary_safety, y0 + boundary_safety,
                x1 - boundary_safety, y1 - boundary_safety)
    def _inside_inset(x, y):
        xi0, yi0, xi1, yi1 = _inset_rect()
        return (xi0 <= x <= xi1) and (yi0 <= y <= yi1)
    def _clip_first_exit_point_rect(x, y, xn, yn):
        xi0, yi0, xi1, yi1 = _inset_rect()
        dx, dy = xn - x, yn - y
        p = [-dx, dx, -dy, dy]; q = [x - xi0, xi1 - x, y - yi0, yi1 - y]
        u0, u1 = 0.0, 1.0
        for pi, qi in zip(p, q):
            if abs(pi) < 1e-12:
                if qi < 0.0: return (xn, yn), False
                continue
            r = qi / pi
            if pi < 0: u0 = max(u0, r)
            else:      u1 = min(u1, r)
            if u0 > u1: return (xn, yn), False
        t = max(0.0, min(1.0, u1))
        return (x + t*dx, y + t*dy), True

    # --- single shared value-noise + curl field (same seed → coherent look) ---
    seed = random_ini
    invS = 1.0 / max(1e-6, float(noise_scale))
    def _rand(i, j):
        t = (i*127.1 + j*311.7 + seed*17.17)
        return (math.sin(t) * 43758.5453123) % 1.0
    def _smooth(t): return t*t*t*(t*(t*6 - 15) + 10)
    def _value(X, Y):
        i0, j0 = math.floor(X), math.floor(Y); fx, fy = X-i0, Y-j0
        sx, sy = _smooth(fx), _smooth(fy)
        v00 = _rand(i0,   j0); v10 = _rand(i0+1, j0)
        v01 = _rand(i0,   j0+1); v11 = _rand(i0+1, j0+1)
        vx0 = v00*(1-sx) + v10*sx; vx1 = v01*(1-sx) + v11*sx
        return vx0*(1-sy) + vx1*sy
    def _grad_mm(x, y):
        X, Y = x*invS, y*invS; h = 1e-2
        dVdX = (_value(X+h, Y) - _value(X-h, Y)) / (2*h)
        dVdY = (_value(X, Y+h) - _value(X, Y-h)) / (2*h)
        return (dVdX*invS, dVdY*invS)
    def curl_vec(x, y):
        dVdx, dVdy = _grad_mm(x, y)
        return (+dVdy, -dVdx)

    # --- outward drift from given pot & repulsion from other pots --------------
    def outward_dir(x, y, Px, Py):
        vx, vy = x - Px, y - Py
        L = math.hypot(vx, vy) or 1.0
        return (vx/L, vy/L)
    def repel_from_other_pots(x, y, idx_current):
        rx, ry = 0.0, 0.0
        R = max(1e-6, repulse_radius_mm)
        for j, pj in enumerate(pots_norm):
            if j == idx_current: continue
            qx, qy = pj.x, pj.y
            dx, dy = x - qx, y - qy
            d = math.hypot(dx, dy)
            if d < R:
                w = repulse_gain * (1.0 - (d/R))**2
                ux, uy = (dx/(d or 1.0), dy/(d or 1.0))
                rx += w * ux
                ry += w * uy
        return rx, ry

    # --- color per pot (for preview). plot_plan should honor Polyline._plot_color ---
    def _color_for_pot(k, default="#1f77b4"):
        try:
            c = getattr(pots_norm[k], "color", None)
            if c: return c
        except Exception:
            pass
        palette = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
                   "#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf"]
        return palette[k % len(palette)] if k is not None else default

    rng = random.Random(random_ini)
    pat = Pattern()

    # --- process pots sequentially --------------------------------------------
    for pi, n_agents in zip(pot_indices, counts):
        Px, Py = pots_norm[pi].x, pots_norm[pi].y
        col = _color_for_pot(pi)

        # 1) generate all agent paths for this pot
        strokes: List[List[XY]] = []
        for _ in range(max(0, int(n_agents))):
            theta0 = rng.uniform(0, 2*math.pi)
            ux0, uy0 = math.cos(theta0), math.sin(theta0)

            xi0, yi0, xi1, yi1 = _inset_rect()
            p0 = (max(xi0, min(xi1, Px - lead_in_mm*ux0)),
                  max(yi0, min(yi1, Py - lead_in_mm*uy0)))
            p1 = (Px, Py)
            p2 = (max(xi0, min(xi1, Px + lead_out_mm*ux0)),
                  max(yi0, min(yi1, Py + lead_out_mm*uy0)))

            pts: List[XY] = [p0, p1, p2]
            x, y = p2

            for k in range(int(steps)):
                cx, cy = curl_vec(x, y)
                ox, oy = outward_dir(x, y, Px, Py)
                rx, ry = repel_from_other_pots(x, y, pi)

                vx = curl_strength*cx + drift_gain*ox + rx
                vy = curl_strength*cy + drift_gain*oy + ry

                if jitter_deg > 0:
                    j = math.radians(rng.uniform(-jitter_deg, jitter_deg))
                    cj, sj = math.cos(j), math.sin(j)
                    vx, vy = (cj*vx - sj*vy, sj*vx + cj*vy)

                L = math.hypot(vx, vy)
                if L < 1e-9:
                    vx, vy = ox, oy; L = 1.0
                nx, ny = (vx/L, vy/L)
                xn = x + step_mm*nx
                yn = y + step_mm*ny

                if not _inside_inset(xn, yn):
                    if stop_at_boundary:
                        (xh, yh), hit = _clip_first_exit_point_rect(x, y, xn, yn)
                        if hit: pts.append((xh, yh))
                    break

                pts.append((xn, yn))
                x, y = xn, yn

            if len(pts) >= 3:
                strokes.append(pts)

        # 2) optional ordering by average direction from pot (for nicer layering)
        if order_by_direction and strokes:
            def _angle_for_path(pts):
                cx = sum(p[0] for p in pts)/len(pts)
                cy = sum(p[1] for p in pts)/len(pts)
                return math.atan2(cy - Py, cx - Px)
            strokes.sort(key=_angle_for_path)

        # 3) add to Pattern (progressive or single pass), with per-pot color tag
        if not progressive:
            for pts in strokes:
                poly = Polyline(
                    pts=pts,
                    z_mode=z_mode,
                    z_threshold=z_threshold,
                    pos_offset=pos_off,
                    settle_s=settle_s,
                    min_seg_mm=min_seg_mm,
                    max_seg_mm=max_seg_mm,
                    feed_draw=feed_draw,
                )
                setattr(poly, "_plot_color", col)
                pat.add(poly)
        else:
            K = max(1, int(progressive_increments))
            for pts in strokes:
                N = len(pts)
                for i in range(1, K+1):
                    m = max(3, int(round((i/K)*N)))
                    sub = pts[:m]
                    if progressive_reverse and (i % 2 == 0):
                        sub = list(reversed(sub))
                    poly = Polyline(
                        pts=sub,
                        z_mode="threshold",
                        pos_offset=pos_off,
                        settle_s=settle_s,
                        min_seg_mm=min_seg_mm,
                        max_seg_mm=max_seg_mm,
                        feed_draw=feed_draw,
                    )
                    setattr(poly, "_plot_color", col)
                    pat.add(poly)

    # --- preview / execute ------------------------------------------------------
    if plot:
        fk = dict(step=50.0, figsize=(9.6, 6.2),
                  title="Curl-Noise Push — multi-pot (repel, per-pot counts & colors)")
        if figure_kwargs: fk.update(figure_kwargs)
        plot_plan((cfg.x_max, cfg.y_max), rect, pots_norm, pat, **fk)  # plotter colors read from Polyline._plot_color

    if execute:
        Renderer(grbl).run(pat)

    return pat





