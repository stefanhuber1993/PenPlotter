"""Configuration models for the pen plotter application."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Workspace:
    """Physical dimensions of the plotting surface."""

    width_mm: float = 300.0
    height_mm: float = 245.0

    def as_tuple(self) -> tuple[float, float]:
        return self.width_mm, self.height_mm


@dataclass
class ServoCalibration:
    """Servo calibration expressed as raw PWM values."""

    up: int = 40
    down: int = 90

    def clamp(self, value: float) -> float:
        return max(0.0, min(1.0, value))

    def to_pwm(self, value: float) -> int:
        value = self.clamp(value)
        return int(round(self.down + value * (self.up - self.down)))


@dataclass
class PenConfig:
    """Configuration values for a single pen."""

    name: str
    color: str
    enabled: bool = True
    feed_rate: int = 3000


@dataclass
class PlotterSettings:
    """Aggregate settings for the GRBL controller and workspace."""

    baudrate: int = 115200
    read_timeout: float = 1.0
    workspace: Workspace = field(default_factory=Workspace)
    servo: ServoCalibration = field(default_factory=ServoCalibration)
    travel_feed: int = 3000


@dataclass
class AppState:
    """Mutable application state shared between UI and backend."""

    available_ports: List[str] = field(default_factory=list)
    connected_port: Optional[str] = None
    pens: Dict[str, PenConfig] = field(default_factory=dict)
    settings: PlotterSettings = field(default_factory=PlotterSettings)
    current_document_name: Optional[str] = None
