"""Example script that generates a spiral pattern and sends it to the server."""
from __future__ import annotations

import math
import requests

from penplotter.geometry import Pattern, Polyline


def build_spiral(turns: int = 10, radius: float = 100.0, steps: int = 800) -> Pattern:
    pts = []
    for i in range(steps):
        t = i / (steps - 1)
        angle = turns * 2 * math.pi * t
        r = radius * t
        x = r * math.cos(angle)
        y = r * math.sin(angle)
        pts.append((x + radius, y + radius))
    pat = Pattern()
    pat.add(Polyline(pts=pts, pen_id=0))
    return pat


def main() -> None:
    pattern = build_spiral()
    payload = pattern.to_dict()
    res = requests.post("http://localhost:8000/api/pattern", json=payload, timeout=5)
    res.raise_for_status()
    print(res.json())


if __name__ == "__main__":
    main()
