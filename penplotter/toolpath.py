"""Toolpath generation utilities."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from .config import PenConfig, Workspace
from .svg_loader import SVGDocument


@dataclass
class Transform:
    """Geometric transform applied to the SVG content."""

    scale: float = 1.0
    rotation_deg: float = 0.0
    offset_x: float = 0.0
    offset_y: float = 0.0
    origin_x: float = 0.0
    origin_y: float = 0.0

    def apply(self, x: float, y: float) -> Tuple[float, float]:
        x -= self.origin_x
        y -= self.origin_y
        theta = math.radians(self.rotation_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        sx = x * self.scale
        sy = y * self.scale
        rx = sx * cos_t - sy * sin_t
        ry = sx * sin_t + sy * cos_t
        return rx + self.offset_x, ry + self.offset_y


@dataclass
class PenToolpath:
    color: str
    pen: PenConfig
    polylines: List[List[Tuple[float, float]]]


def generate_toolpaths(
    document: SVGDocument,
    pens: Dict[str, PenConfig],
    transform: Transform,
    *,
    tolerance: float = 0.5,
) -> List[PenToolpath]:
    """Generate polylines grouped by pen configuration."""

    grouped = document.sampled_polylines(tolerance)
    toolpaths: List[PenToolpath] = []
    for color, polylines in grouped.items():
        pen = pens.get(color)
        if not pen or not pen.enabled:
            continue
        transformed: List[List[Tuple[float, float]]] = []
        for polyline in polylines:
            transformed.append([transform.apply(x, y) for x, y in polyline])
        toolpaths.append(PenToolpath(color=color, pen=pen, polylines=transformed))
    return toolpaths


def transformed_bounds(toolpaths: Iterable[PenToolpath]) -> Tuple[float, float, float, float]:
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    for toolpath in toolpaths:
        for poly in toolpath.polylines:
            for x, y in poly:
                xmin = min(xmin, x)
                ymin = min(ymin, y)
                xmax = max(xmax, x)
                ymax = max(ymax, y)
    if xmin == float("inf"):
        return (0.0, 0.0, 0.0, 0.0)
    return xmin, xmax, ymin, ymax


def fits_workspace(bounds: Tuple[float, float, float, float], workspace: Workspace) -> bool:
    xmin, xmax, ymin, ymax = bounds
    if xmax - xmin > workspace.width_mm + 1e-6:
        return False
    if ymax - ymin > workspace.height_mm + 1e-6:
        return False
    if xmin < 0 or ymin < 0:
        return False
    return True
