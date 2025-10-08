"""Utilities for parsing SVG files into toolpath friendly data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from svgpathtools import Path as SVGPathObject, svg2paths2


@dataclass
class SVGShape:
    """Single drawable item extracted from the SVG."""

    path: SVGPathObject
    color: str
    stroke_width: float = 1.0

    def bounds(self) -> Tuple[float, float, float, float]:
        xmin, xmax, ymin, ymax = self.path.bbox()
        return float(xmin), float(xmax), float(ymin), float(ymax)


@dataclass
class SVGDocument:
    """Representation of an SVG document as a collection of shapes grouped by color."""

    shapes: List[SVGShape] = field(default_factory=list)
    svg_attributes: Dict[str, str] = field(default_factory=dict)
    source_path: Optional[Path] = None

    @classmethod
    def from_file(cls, path: Path) -> "SVGDocument":
        paths, attributes, svg_attributes = svg2paths2(str(path))
        shapes = []
        for path_obj, attr in zip(paths, attributes):
            color = attr.get("stroke") or attr.get("fill") or "#000000"
            width = float(attr.get("stroke-width", "1"))
            shapes.append(SVGShape(path=path_obj, color=color, stroke_width=width))
        return cls(shapes=shapes, svg_attributes=svg_attributes, source_path=Path(path))

    @classmethod
    def from_bytes(cls, data: bytes, *, name: str = "uploaded.svg") -> "SVGDocument":
        temp_path = Path(name)
        paths, attributes, svg_attributes = svg2paths2(bytestring=data)
        shapes = []
        for path_obj, attr in zip(paths, attributes):
            color = attr.get("stroke") or attr.get("fill") or "#000000"
            width = float(attr.get("stroke-width", "1"))
            shapes.append(SVGShape(path=path_obj, color=color, stroke_width=width))
        return cls(shapes=shapes, svg_attributes=svg_attributes, source_path=temp_path)

    def bounds(self) -> Tuple[float, float, float, float]:
        if not self.shapes:
            return (0.0, 0.0, 0.0, 0.0)
        xmin, xmax, ymin, ymax = self.shapes[0].bounds()
        for shape in self.shapes[1:]:
            sx0, sx1, sy0, sy1 = shape.bounds()
            xmin = min(xmin, sx0)
            xmax = max(xmax, sx1)
            ymin = min(ymin, sy0)
            ymax = max(ymax, sy1)
        return xmin, xmax, ymin, ymax

    def group_by_color(self) -> Dict[str, List[SVGShape]]:
        groups: Dict[str, List[SVGShape]] = {}
        for shape in self.shapes:
            groups.setdefault(shape.color, []).append(shape)
        return groups

    def to_svg(self) -> str:
        xmin, xmax, ymin, ymax = self.bounds()
        width = xmax - xmin
        height = ymax - ymin
        width = width or 1.0
        height = height or 1.0
        viewbox = f"{xmin} {ymin} {width} {height}"
        body = []
        for shape in self.shapes:
            body.append(
                f'<path d="{shape.path.d()}" stroke="{shape.color}" stroke-width="{shape.stroke_width}" fill="none" />'
            )
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" '
            f'width="{width}" height="{height}">' + "".join(body) + "</svg>"
        )
        return svg

    def sampled_polylines(self, tolerance: float = 0.5) -> Dict[str, List[List[Tuple[float, float]]]]:
        """Approximate each path as a list of points grouped by color."""

        polylines: Dict[str, List[List[Tuple[float, float]]]] = {}
        for shape in self.shapes:
            polyline = sample_path(shape.path, tolerance)
            if len(polyline) < 2:
                continue
            polylines.setdefault(shape.color, []).append(polyline)
        return polylines


def sample_path(path: SVGPathObject, tolerance: float = 0.5) -> List[Tuple[float, float]]:
    """Convert an svgpathtools Path into a list of coordinate tuples."""

    length = max(path.length(), tolerance)
    steps = max(int(length / max(tolerance, 1e-3)), 1)
    points: List[Tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        point = path.point(t)
        points.append((float(point.real), float(point.imag)))
    return points
