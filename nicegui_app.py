"""Standalone NiceGUI application that recreates the existing pen plotter widget UI.

Run with:
    python nicegui_app.py [--serial-device /dev/tty.usbserial-A50285BI] [--area-size WIDTHxHEIGHT]
and open the reported URL in a browser.

The optional --area-size flag defines the maximum bed dimensions to display (in millimeters).
"""

from __future__ import annotations

import argparse
import os
import math
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

from nicegui import events, ui


DEFAULT_BED_SIZE: Tuple[float, float] = (300.0, 245.0)
DEFAULT_RECT_SIZE: Tuple[float, float] = (300.0, 245.0)
APP_CONFIG: Dict[str, Any] = {"serial_device": None, "bed_size": DEFAULT_BED_SIZE}


@dataclass
class Pot:
    """Simple representation of a color sampling pot."""

    identifier: int
    color: str = "#3a86ff"
    height: float = 1.0
    position: tuple[float, float] = (0.0, 0.0)


@dataclass
class PlotterState:
    """Aggregates all mutable UI state for the mock implementation."""

    workpiece: str = "A4"
    z_height: float = 1.0
    status_lines: List[str] = field(default_factory=lambda: ["Ready. Configure the plotter to begin."])
    pots: List[Pot] = field(default_factory=list)
    next_pot_id: int = 1
    selected_pot_id: Optional[int] = None
    bed_width: float = DEFAULT_BED_SIZE[0]
    bed_height: float = DEFAULT_BED_SIZE[1]
    rect_min: Tuple[float, float] = (0.0, 0.0)
    rect_max: Tuple[float, float] = (0.0, 0.0)
    corner_heights: Dict[str, float] = field(
        default_factory=lambda: {"BL": 1.0, "BR": 1.0, "TL": 1.0, "TR": 1.0}
    )
    selected_entity: Optional[Tuple[str, Union[str, int]]] = ("corner", "BL")
    drag_entity: Optional[Tuple[str, Union[str, int]]] = None
    drag_arm: Optional[Tuple[str, Union[str, int]]] = None
    drag_has_moved: bool = False
    serial_device: Optional[str] = None

    def log(self, message: str) -> None:
        self.status_lines.append(message)
        if len(self.status_lines) > 200:
            del self.status_lines[: len(self.status_lines) - 200]


class PlotterApp:
    """Encapsulates layout creation and interactions for the NiceGUI app."""

    def __init__(
        self,
        *,
        serial_device: Optional[str] = None,
        bed_size: Tuple[float, float] = DEFAULT_BED_SIZE,
    ) -> None:
        self.state = PlotterState()
        self.state.serial_device = serial_device
        self.status_log = None
        self.pot_select = None
        self.progress = None
        self.progress_label = None
        self.workpiece_select = None
        self.color_picker = None
        self.canvas = None
        self.canvas_element = None
        self.canvas_size = (700, 600)
        self.canvas_margin = 32
        self._last_pointer_pos: Optional[Tuple[float, float]] = None
        self.z_slider = None
        self._suppress_height_event = False
        self._suppress_color_event = False
        self.area_label = None
        self._suppress_pot_event = False
        self.show_area_overlay = True
        self.show_pattern_overlay = True
        self.show_pots_overlay = True
        self.area_toggle = None
        self.pattern_toggle = None
        self.pots_toggle = None
        self.status_summary = None
        self.recent_status_container = None
        self._apply_bed_size(bed_size)
        self._initialize_default_rectangle()
        if serial_device:
            self.state.log(f"Using serial device: {serial_device}")

    def _compact_button(self, label: str, on_click, *, color: Optional[str] = None) -> ui.button:
        button = ui.button(label, on_click=on_click, color=color)
        button.props("unelevated dense size='sm'")
        button.classes("px-2 py-1 text-xs")
        return button

    def _toggle_button(self, label: str, handler, state: bool) -> ui.button:
        button = ui.button(label, on_click=handler).props("unelevated size='sm'")
        button.classes("px-2 py-1 text-xs rounded transition-colors duration-150")
        self._apply_toggle_style(button, state)
        return button

    @staticmethod
    def _apply_toggle_style(button: ui.button, state: bool) -> None:
        button.classes(remove="toggle-btn-active toggle-btn-inactive")
        button.classes(add="toggle-btn-active" if state else "toggle-btn-inactive")

    def _apply_bed_size(self, bed_size: Tuple[float, float]) -> None:
        try:
            width, height = bed_size
        except Exception:
            width, height = DEFAULT_BED_SIZE
        width = max(10.0, float(width))
        height = max(10.0, float(height))
        self.state.bed_width = width
        self.state.bed_height = height

    def _initialize_default_rectangle(self) -> None:
        rect_width, rect_height = DEFAULT_RECT_SIZE
        rect_width = min(rect_width, self.state.bed_width)
        rect_height = min(rect_height, self.state.bed_height)
        min_x = max(0.0, (self.state.bed_width - rect_width) / 2.0)
        min_y = max(0.0, (self.state.bed_height - rect_height) / 2.0)
        self.state.rect_min = (min_x, min_y)
        self.state.rect_max = (min_x + rect_width, min_y + rect_height)
        self.state.log(
            f"Initial work area set to {rect_width:.0f} × {rect_height:.0f} mm within bed "
            f"{self.state.bed_width:.0f} × {self.state.bed_height:.0f} mm."
        )

    def _toggle_area(self) -> None:
        self.show_area_overlay = not self.show_area_overlay
        self._apply_toggle_style(self.area_toggle, self.show_area_overlay)
        if not self.show_area_overlay:
            if self.state.selected_entity and self.state.selected_entity[0] == "corner":
                self.state.selected_entity = None
            self._update_selection_label()
        else:
            self._select_entity(("corner", "BL"))
        self._update_canvas()

    def _toggle_pattern(self) -> None:
        self.show_pattern_overlay = not self.show_pattern_overlay
        self._apply_toggle_style(self.pattern_toggle, self.show_pattern_overlay)
        self._update_canvas()

    def _toggle_pots(self) -> None:
        self.show_pots_overlay = not self.show_pots_overlay
        self._apply_toggle_style(self.pots_toggle, self.show_pots_overlay)
        if not self.show_pots_overlay:
            self.state.selected_pot_id = None
            if self.state.selected_entity and self.state.selected_entity[0] == "pot":
                self.state.selected_entity = None
            self._update_selection_label()
        self._update_canvas()

    def _normalize_entity(
        self, entity: Optional[Tuple[str, Union[str, int]]]
    ) -> Optional[Tuple[str, Union[str, int]]]:
        if entity is None:
            return None
        kind, key = entity
        if kind == "corner":
            return (kind, str(key))
        if kind == "pot":
            try:
                return (kind, int(key))
            except (TypeError, ValueError):
                return None
        return entity

    def _is_entity_draggable(self, entity: Optional[Tuple[str, Union[str, int]]]) -> bool:
        normalized = self._normalize_entity(entity)
        if normalized is None:
            return False
        kind, key = normalized
        if kind == "corner":
            return key in {"BL", "TR"}
        if kind == "pot":
            return True
        return False

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------
    def create(self) -> None:
        with ui.header().classes("items-center justify-between bg-primary text-white py-2 px-3"):
            with ui.row().classes("items-center gap-2"):
                ui.label("Pen Plotter Control Suite").classes("text-lg font-semibold")
                if self.state.serial_device:
                    ui.label(f"Device: {self.state.serial_device}").classes(
                        "text-xs font-medium px-2 py-0.5 bg-white text-primary rounded"
                    )
            self.workpiece_select = ui.select(
                ["A4", "A5", "15 cm", "10 cm"],
                value=self.state.workpiece,
                on_change=self._on_workpiece_change,
            ).props("label='Workpiece' dense")

        with ui.column().classes("w-full mx-auto p-4 gap-4"):
            with ui.row().classes("w-full gap-4 items-stretch flex-nowrap").style("min-height: calc(100vh - 140px);"):
                with ui.column().classes("gap-3 h-full").style("flex:0 0 70%;max-width:70%;"):
                    self._build_canvas_section()
                with ui.column().classes("h-full w-full").style("flex:1 1 30%;max-width:30%;"):
                    self._build_control_tabs()
        if self.state.selected_entity:
            self._select_entity(self.state.selected_entity)
        else:
            self._select_entity(("corner", "BL"))

    # ------------------------------------------------------------------
    # Canvas and overlay mock
    # ------------------------------------------------------------------
    def _build_canvas_section(self) -> None:
        with ui.card().classes("flex-1 min-w-[340px] p-3 gap-3"):
            with ui.row().classes("gap-2 flex-wrap items-center"):
                ui.label("Plotting bed").classes("text-sm font-medium")
                self.area_label = ui.label("").classes("text-xs text-gray-500")
            with ui.row().classes("gap-2 flex-wrap items-center"):
                ui.label("Jog & Controls").classes("text-xs uppercase tracking-wide text-gray-500")
                self._compact_button("Home", lambda: self._notify("Homed axes."))
                self._compact_button("Sweep", lambda: self._notify("Swept rectangle."))
                self._compact_button("Pen Up", lambda: self._notify("Moved pen up."))
                self._compact_button("Pen Down", lambda: self._notify("Moved pen down."))
                self.area_toggle = self._toggle_button("Toggle Area", self._toggle_area, self.show_area_overlay)
                self.pattern_toggle = self._toggle_button("Toggle Pattern", self._toggle_pattern, self.show_pattern_overlay)
                self.pots_toggle = self._toggle_button("Toggle Pots", self._toggle_pots, self.show_pots_overlay)
            self.canvas = ui.html(
                content=self._render_canvas(),
                sanitize=False,
            ).classes("rounded-lg border bg-slate-50 w-full").style(
                f"width:100%; aspect-ratio:{self.state.bed_width}/{self.state.bed_height};"
                "touch-action:none;cursor:crosshair;"
            )
            self.canvas.on("pointerdown", self._handle_canvas_pointer_down)
            self.canvas.on("pointermove", self._handle_canvas_pointer_move)
            self.canvas.on("pointerup", self._handle_canvas_pointer_up)
            self.canvas.on("pointerleave", self._handle_canvas_pointer_up)
            with ui.row().classes("mt-1 gap-2 text-xs text-gray-500"):
                ui.icon("touch_app").classes("text-primary")
                ui.label("Click to jog corners or drag handles to reshape the work area.")

        with ui.column().classes("w-full gap-2"):
            self.status_summary = ui.card().classes("p-3 text-xs font-medium bg-slate-100")
            self.status_summary.set_text("Connected | COM3 @ 115200 | X=0.0 | Y=0.0 | Z=0.00 | Idle")
            with ui.card().classes("p-3"):
                ui.label("Recent activity").classes("text-xs font-medium text-gray-600 mb-2")
                self.recent_status_container = ui.column().classes("gap-1 text-xs text-gray-700")
                self._update_status_panels()

    def _build_control_tabs(self) -> None:
        with ui.card().classes("w-full h-full p-0"):
            with ui.tabs().classes("text-xs") as tabs:
                tab_config = ui.tab("Config")
                tab_area = ui.tab("Area")
                tab_pots = ui.tab("Pots")
                tab_console = ui.tab("Console")
                tab_run = ui.tab("Run")
            with ui.tab_panels(tabs, value=tab_config).classes("h-full"):
                with ui.tab_panel(tab_config).classes("h-full overflow-y-auto p-3"):
                    ui.label("Configuration options coming soon.").classes("text-xs text-gray-500")
                with ui.tab_panel(tab_area).classes("h-full overflow-y-auto p-3"):
                    self._build_area_controls()
                with ui.tab_panel(tab_pots).classes("h-full overflow-y-auto p-3"):
                    self._build_pot_controls()
                with ui.tab_panel(tab_console).classes("h-full overflow-y-auto p-3"):
                    self._build_console_tab()
                with ui.tab_panel(tab_run).classes("h-full overflow-y-auto p-3"):
                    self._build_runner_panel()

    def _build_area_controls(self) -> None:
        with ui.column().classes("gap-3"):
            with ui.card().classes("p-3 gap-2"):
                ui.label("Work Area Presets").classes("text-xs uppercase tracking-wide text-gray-500")
                with ui.row().classes("gap-2 flex-wrap"):
                    self._compact_button("A4", lambda: self._quick_size("A4"))
                    self._compact_button("A5", lambda: self._quick_size("A5"))
                    self._compact_button("15 cm", lambda: self._quick_size("15 cm"))
                    self._compact_button("10 cm", lambda: self._quick_size("10 cm"))
                    self._compact_button("Reset Z Heights", self._reset_all_z_heights)
            with ui.card().classes("p-3 gap-3 items-center"):
                ui.label("Z Height").classes("text-xs uppercase tracking-wide text-gray-500")
                self.z_slider_container = ui.column().classes("items-center")
                with self.z_slider_container:
                    self.z_slider = ui.slider(
                        min=0.0,
                        max=1.0,
                        step=0.01,
                        value=self.state.z_height,
                        on_change=self._on_height_change,
                    ).props("vertical reverse label-always").style("height:240px;width:2.2rem;")

    def _build_console_tab(self) -> None:
        with ui.card().classes("p-3 gap-2"):
            ui.label("G-code Console").classes("text-sm font-medium")
            console_input = ui.textarea(placeholder="Enter G-code commands...").props("autogrow")
            with ui.row().classes("gap-2"):
                ui.button(
                    "Send",
                    on_click=lambda: self._log_status(f"Sent G-code: {console_input.value.strip()}"),
                    color="primary",
                ).props("unelevated size='sm'")
                ui.button("Clear", on_click=lambda: console_input.set_value("")).props("unelevated size='sm'")

    def _canvas_transform(self) -> Tuple[float, float, float]:
        inner_width = self.canvas_size[0] - 2 * self.canvas_margin
        inner_height = self.canvas_size[1] - 2 * self.canvas_margin
        if self.state.bed_width <= 0 or self.state.bed_height <= 0:
            return 1.0, self.canvas_margin, self.canvas_margin
        scale_x = inner_width / self.state.bed_width
        scale_y = inner_height / self.state.bed_height
        scale = min(scale_x, scale_y)
        used_width = self.state.bed_width * scale
        used_height = self.state.bed_height * scale
        offset_x = self.canvas_margin + (inner_width - used_width) / 2.0
        offset_y = self.canvas_margin + (inner_height - used_height) / 2.0
        return scale, offset_x, offset_y

    def _world_to_canvas(self, x: float, y: float) -> Tuple[float, float]:
        scale, offset_x, offset_y = self._canvas_transform()
        cx = offset_x + x * scale
        cy = self.canvas_size[1] - (offset_y + y * scale)
        return cx, cy

    def _canvas_to_world(self, cx: float, cy: float) -> Tuple[float, float]:
        scale, offset_x, offset_y = self._canvas_transform()
        if scale <= 0:
            return 0.0, 0.0
        x = (cx - offset_x) / scale
        y = (self.canvas_size[1] - cy - offset_y) / scale
        x = max(0.0, min(self.state.bed_width, x))
        y = max(0.0, min(self.state.bed_height, y))
        return x, y

    def _corner_world_coords(self, key: str) -> Tuple[float, float]:
        min_x, min_y = self.state.rect_min
        max_x, max_y = self.state.rect_max
        positions = {
            "BL": (min_x, min_y),
            "BR": (max_x, min_y),
            "TL": (min_x, max_y),
            "TR": (max_x, max_y),
        }
        return positions[key]

    def _render_canvas(self) -> str:
        width, height = self.canvas_size
        bed_left, bed_bottom = self._world_to_canvas(0.0, 0.0)
        bed_right, _ = self._world_to_canvas(self.state.bed_width, 0.0)
        _, bed_top = self._world_to_canvas(0.0, self.state.bed_height)
        bed_width_px = max(1.0, bed_right - bed_left)
        bed_height_px = max(1.0, bed_bottom - bed_top)

        vertical_lines = []
        tick = 50.0
        x = 0.0
        while x <= self.state.bed_width + 1e-6:
            cx, _ = self._world_to_canvas(x, 0.0)
            vertical_lines.append(
                f'<line x1="{cx:.1f}" y1="{bed_top:.1f}" '
                f'x2="{cx:.1f}" y2="{bed_bottom:.1f}" />'
            )
            x += tick

        horizontal_lines = []
        y = 0.0
        while y <= self.state.bed_height + 1e-6:
            _, cy = self._world_to_canvas(0.0, y)
            horizontal_lines.append(
                f'<line x1="{bed_left:.1f}" y1="{cy:.1f}" x2="{bed_right:.1f}" y2="{cy:.1f}" />'
            )
            y += tick

        rect_items: List[str] = []
        corner_items: List[str] = []
        if self.show_area_overlay:
            rect_min_x, rect_min_y = self.state.rect_min
            rect_max_x, rect_max_y = self.state.rect_max
            rect_left, rect_bottom = self._world_to_canvas(rect_min_x, rect_min_y)
            rect_right, rect_top = self._world_to_canvas(rect_max_x, rect_max_y)
            rect_x = rect_left
            rect_y = rect_top
            rect_width = max(1.0, rect_right - rect_left)
            rect_height = max(1.0, rect_bottom - rect_top)
            rect_items.append(
                f'<rect x="{rect_x:.1f}" y="{rect_y:.1f}" width="{rect_width:.1f}" height="{rect_height:.1f}" '
                f'fill="rgba(37, 99, 235, 0.08)" stroke="#2563eb" stroke-width="2" />'
            )

        label_offsets = {
            "BL": (-16, 20),
            "BR": (16, 20),
            "TL": (-16, -16),
            "TR": (16, -16),
        }
        selected = self.state.selected_entity
        if self.show_area_overlay:
            for key in ["BL", "BR", "TL", "TR"]:
                cx, cy = self._world_to_canvas(*self._corner_world_coords(key))
                is_selected = selected == ("corner", key)
                corner_items.append(
                    f'<g>'
                    f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{10 if is_selected else 8}" '
                    f'stroke="#2563eb" stroke-width="{3 if is_selected else 2}" fill="#fff" />'
                    f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{4}" fill="#2563eb" />'
                    f'</g>'
                )
                offset_x, offset_y = label_offsets[key]
                label_x = cx + offset_x
                label_y = cy + offset_y
                corner_items.append(
                    f'<text x="{label_x:.1f}" y="{label_y:.1f}" '
                    f'font-size="12" text-anchor="middle" fill="#1f2937">{self.state.corner_heights[key]:.2f}</text>'
                )

        pot_items = []
        if self.show_pots_overlay:
            for pot in self.state.pots:
                px, py = self._world_to_canvas(*pot.position)
                is_selected = selected == ("pot", pot.identifier)
                radius = 12 if is_selected else 10
                pot_items.append(
                    f'<g>'
                    f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{radius}" fill="{pot.color}" '
                    f'stroke="#1f2937" stroke-width="{2 if is_selected else 1.5}" />'
                    f'<text x="{px:.1f}" y="{py + radius + 14:.1f}" font-size="11" '
                    f'text-anchor="middle" fill="#1f2937">Pot {pot.identifier}</text>'
                    f'</g>'
                )

        jog_items = []
        jog_layout = self._jog_button_layout(selected)
        for btn in jog_layout:
            x = float(btn["x"])
            y = float(btn["y"])
            btn_width = float(btn["width"])
            btn_height = float(btn["height"])
            label = btn["label"]
            jog_items.append(
                f'<g>'
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{btn_width:.1f}" height="{btn_height:.1f}" '
                f'rx="4" ry="4" fill="#e2e8f0" stroke="#475569" stroke-width="1" />'
                f'<text x="{x + btn_width / 2:.1f}" y="{y + btn_height / 2:.1f}" font-size="11" '
                f'text-anchor="middle" dominant-baseline="middle" fill="#1f2937">{label}</text>'
                f'</g>'
            )

        svg = f"""
        <svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" style="user-select:none;">
          <defs>
            <style>
              .grid-line {{ stroke: #d1d5db; stroke-width: 1; }}
            </style>
          </defs>
          <rect x="{bed_left:.1f}" y="{bed_top:.1f}" width="{bed_width_px:.1f}" height="{bed_height_px:.1f}" fill="#f8fafc" stroke="#4b5563" stroke-width="2" rx="12" />
          <g class="grid-line">
            {''.join(vertical_lines)}
            {''.join(horizontal_lines)}
          </g>
          {''.join(rect_items)}
          {''.join(corner_items)}
          {''.join(pot_items)}
          {''.join(jog_items)}
        </svg>
        """
        return svg

    def _get_entity_canvas_position(
        self, entity: Optional[Tuple[str, Union[str, int]]]
    ) -> Optional[Tuple[float, float, float]]:
        normalized = self._normalize_entity(entity)
        if normalized is None:
            return None
        kind, key = normalized
        if kind == "corner":
            if not self.show_area_overlay:
                return None
            cx, cy = self._world_to_canvas(*self._corner_world_coords(key))
            radius = 10.0
            return cx, cy, radius
        if kind == "pot":
            if not self.show_pots_overlay:
                return None
            pot = next((p for p in self.state.pots if p.identifier == key), None)
            if pot is None:
                return None
            px, py = self._world_to_canvas(*pot.position)
            radius = 12.0
            return px, py, radius
        return None

    def _jog_button_layout(self, entity: Optional[Tuple[str, Union[str, int]]]) -> List[Dict[str, float | str]]:
        if not self._is_entity_draggable(entity):
            return []
        if isinstance(entity, tuple):
            if entity[0] == "corner" and not self.show_area_overlay:
                return []
            if entity[0] == "pot" and not self.show_pots_overlay:
                return []
        position = self._get_entity_canvas_position(entity)
        if position is None:
            return []
        base_x, base_y, radius = position
        btn_size = 14.0
        gap = 1.0
        first_offset = radius + 10.0
        # Upwards (positive Y)
        up_near_y = base_y - first_offset - btn_size
        up_far_y = up_near_y - (btn_size + gap)
        # Downwards (negative Y)
        down_near_y = base_y + first_offset
        down_far_y = down_near_y + btn_size + gap
        # Right (positive X)
        right_near_x = base_x + first_offset
        right_far_x = right_near_x + btn_size + gap
        # Left (negative X)
        left_near_x = base_x - first_offset - btn_size
        left_far_x = left_near_x - (btn_size + gap)

        layout: List[Dict[str, float | str]] = []

        def add_button(x: float, y: float, label: str, dx: float, dy: float) -> None:
            layout.append(
                {
                    "x": x,
                    "y": y,
                    "width": btn_size,
                    "height": btn_size,
                    "label": label,
                    "dx": dx,
                    "dy": dy,
                }
            )

        # Up (+Y)
        add_button(base_x - btn_size / 2, up_near_y, "+", 0.0, +0.1)
        add_button(base_x - btn_size / 2, up_far_y, "++", 0.0, +1.0)
        # Down (-Y)
        add_button(base_x - btn_size / 2, down_near_y, "-", 0.0, -0.1)
        add_button(base_x - btn_size / 2, down_far_y, "--", 0.0, -1.0)
        # Right (+X)
        add_button(right_near_x, base_y - btn_size / 2, "+", +0.1, 0.0)
        add_button(right_far_x, base_y - btn_size / 2, "++", +1.0, 0.0)
        # Left (-X)
        add_button(left_near_x, base_y - btn_size / 2, "-", -0.1, 0.0)
        add_button(left_far_x, base_y - btn_size / 2, "--", -1.0, 0.0)

        return layout

    def _update_canvas(self) -> None:
        if self.canvas is not None:
            self.canvas.set_content(self._render_canvas())
        self._update_area_label()

    def _hit_test_canvas(self, cx: float, cy: float) -> Optional[Tuple[str, Union[str, int]]]:
        if self.show_area_overlay:
            for key in ["BL", "BR", "TL", "TR"]:
                hx, hy = self._world_to_canvas(*self._corner_world_coords(key))
                if math.hypot(cx - hx, cy - hy) <= 14:
                    return ("corner", key)
        if self.show_pots_overlay:
            for pot in reversed(self.state.pots):
                px, py = self._world_to_canvas(*pot.position)
                if math.hypot(cx - px, cy - py) <= 16:
                    return ("pot", pot.identifier)
        return None

    def _hit_test_jog(self, cx: float, cy: float) -> Optional[Dict[str, Union[float, Tuple[str, Union[str, int]]]]]:
        selected = self.state.selected_entity
        if not self._is_entity_draggable(selected):
            return None
        for btn in self._jog_button_layout(selected):
            x = float(btn["x"])
            y = float(btn["y"])
            width = float(btn["width"])
            height = float(btn["height"])
            if x <= cx <= x + width and y <= cy <= y + height:
                return {
                    "entity": self._normalize_entity(selected),
                    "dx": float(btn["dx"]),
                    "dy": float(btn["dy"]),
                }
        return None

    def _handle_canvas_pointer_down(self, e: events.GenericEventArguments) -> None:
        data = e.args or {}
        cx = float(data.get("offsetX", 0.0))
        cy = float(data.get("offsetY", 0.0))
        jog_hit = self._hit_test_jog(cx, cy)
        if jog_hit:
            entity = jog_hit["entity"]
            dx = float(jog_hit["dx"])
            dy = float(jog_hit["dy"])
            if entity:
                self._apply_jog(entity, dx, dy)
            self.state.drag_entity = None
            self.state.drag_has_moved = False
            self._last_pointer_pos = None
            return
        target = self._normalize_entity(self._hit_test_canvas(cx, cy))
        if target is None:
            self.state.drag_entity = None
            self.state.drag_arm = None
            return

        previous = self.state.selected_entity
        if previous != target:
            self._select_entity(target)
            if self._is_entity_draggable(target):
                self.state.drag_arm = target
            return

        if not self._is_entity_draggable(target):
            self._select_entity(target)
            self.state.drag_entity = None
            self.state.drag_arm = None
            return

        if self.state.drag_arm == target:
            self.state.drag_entity = target
            self.state.drag_has_moved = False
            self._last_pointer_pos = (cx, cy)
            self.state.drag_arm = None
        else:
            self.state.drag_entity = None
            self.state.drag_has_moved = False
            self.state.drag_arm = target

    def _handle_canvas_pointer_move(self, e: events.GenericEventArguments) -> None:
        if not self.state.drag_entity:
            return
        data = e.args or {}
        if data.get("buttons", 0) == 0:
            return
        cx = float(data.get("offsetX", 0.0))
        cy = float(data.get("offsetY", 0.0))
        if self._last_pointer_pos:
            if math.hypot(cx - self._last_pointer_pos[0], cy - self._last_pointer_pos[1]) > 2:
                self.state.drag_has_moved = True
        self._last_pointer_pos = (cx, cy)
        wx, wy = self._canvas_to_world(cx, cy)
        self._apply_drag(wx, wy)

    def _handle_canvas_pointer_up(self, _: events.GenericEventArguments) -> None:
        if not self.state.drag_entity:
            return
        if not self.state.drag_has_moved:
            self._handle_click_action(self.state.drag_entity)
        self.state.drag_entity = None
        self.state.drag_has_moved = False
        self._last_pointer_pos = None
        self.state.drag_arm = None

    def _handle_click_action(self, entity: Tuple[str, Union[str, int]]) -> None:
        kind, key = entity
        if kind == "corner":
            x, y = self._corner_world_coords(key)
            self._log_status(f"Jogging to corner {key} at ({x:.1f}, {y:.1f}).")
        elif kind == "pot":
            pot = next((p for p in self.state.pots if p.identifier == key), None)
            if pot:
                x, y = pot.position
                self._log_status(f"Jogging to pot #{pot.identifier} at ({x:.1f}, {y:.1f}).")

    def _apply_jog(self, entity: Tuple[str, Union[str, int]], dx: float, dy: float) -> None:
        normalized = self._normalize_entity(entity)
        if normalized is None or not self._is_entity_draggable(normalized):
            return
        kind, key = normalized
        if kind == "corner":
            min_x, min_y = self.state.rect_min
            max_x, max_y = self.state.rect_max
            if key == "BL":
                min_x = min(max(min_x + dx, 0.0), max_x - 1.0)
                min_y = min(max(min_y + dy, 0.0), max_y - 1.0)
                self.state.rect_min = (min_x, min_y)
            elif key == "TR":
                max_x = max(min(max_x + dx, self.state.bed_width), min_x + 1.0)
                max_y = max(min(max_y + dy, self.state.bed_height), min_y + 1.0)
                self.state.rect_max = (max_x, max_y)
            else:
                return
            self._log_status(f"Jogged corner {key} by ({dx:+.2f}, {dy:+.2f}).")
        elif kind == "pot":
            pot = next((p for p in self.state.pots if p.identifier == key), None)
            if pot is None:
                return
            new_x = max(0.0, min(self.state.bed_width, pot.position[0] + dx))
            new_y = max(0.0, min(self.state.bed_height, pot.position[1] + dy))
            pot.position = (new_x, new_y)
            self._log_status(f"Jogged pot #{pot.identifier} to ({new_x:.1f}, {new_y:.1f}).")
        self._update_canvas()
        self._update_selection_label()

    def _apply_drag(self, x: float, y: float) -> None:
        if not self.state.drag_entity:
            return
        if not self._is_entity_draggable(self.state.drag_entity):
            return
        kind, key = self.state.drag_entity
        if kind == "corner":
            self._update_corner_position(str(key), x, y)
        elif kind == "pot":
            self._update_pot_position(int(key), x, y)
        self._update_canvas()
        self._update_selection_label()

    def _update_corner_position(self, key: str, x: float, y: float) -> None:
        key = str(key)
        if key not in {"BL", "TR"}:
            return
        min_x, min_y = self.state.rect_min
        max_x, max_y = self.state.rect_max

        if key == "BL":
            min_x = min(max(x, 0.0), max_x - 1.0)
            min_y = min(max(y, 0.0), max_y - 1.0)
        elif key == "TR":
            max_x = max(min(x, self.state.bed_width), min_x + 1.0)
            max_y = max(min(y, self.state.bed_height), min_y + 1.0)

        self.state.rect_min = (min_x, min_y)
        self.state.rect_max = (max_x, max_y)

    def _update_pot_position(self, identifier: int, x: float, y: float) -> None:
        clamped_x = max(0.0, min(self.state.bed_width, x))
        clamped_y = max(0.0, min(self.state.bed_height, y))
        for pot in self.state.pots:
            if pot.identifier == identifier:
                pot.position = (clamped_x, clamped_y)
                break

    def _default_pot_position(self) -> Tuple[float, float]:
        min_x, min_y = self.state.rect_min
        max_x, max_y = self.state.rect_max
        width = max_x - min_x
        height = max_y - min_y
        target_x = min_x + max(10.0, width) * 0.25
        target_y = min_y + max(10.0, height) * 0.5
        return (
            max(0.0, min(self.state.bed_width, target_x)),
            max(0.0, min(self.state.bed_height, target_y)),
        )

    def _select_entity(self, entity: Tuple[str, Union[str, int]], *, update_slider: bool = True) -> None:
        normalized = self._normalize_entity(entity)
        if normalized is None:
            return
        kind, key = normalized
        self.state.selected_entity = (kind, key)
        self.state.drag_arm = None
        self.state.drag_entity = None
        self.state.drag_has_moved = False
        target_height = self.state.z_height

        if kind == "corner":
            target_height = self.state.corner_heights[key]
            self.state.selected_pot_id = None
            if self.pot_select is not None:
                if self.pot_select.value is not None:
                    try:
                        self._suppress_pot_event = True
                        self.pot_select.value = None
                    finally:
                        self._suppress_pot_event = False
            if self.color_picker is not None:
                self.color_picker.disable()
        elif kind == "pot":
            identifier = key
            self.state.selected_pot_id = key
            if self.pot_select is not None:
                target_value = str(key)
                if self.pot_select.value != target_value:
                    try:
                        self._suppress_pot_event = True
                        self.pot_select.value = target_value
                    finally:
                        self._suppress_pot_event = False
            pot = next((p for p in self.state.pots if p.identifier == key), None)
            if pot:
                target_height = pot.height
                if self.color_picker is not None:
                    try:
                        self._suppress_color_event = True
                        self.color_picker.value = pot.color
                    finally:
                        self._suppress_color_event = False
                    self.color_picker.enable()
            elif self.color_picker is not None:
                self.color_picker.disable()
        elif self.color_picker is not None:
            self.color_picker.disable()

        if update_slider and self.z_slider is not None:
            try:
                self._suppress_height_event = True
                self.z_slider.value = target_height
            finally:
                self._suppress_height_event = False
        self.state.z_height = target_height
        self._update_selection_label()
        self._update_canvas()
    # ------------------------------------------------------------------
    # Height slider and primary actions
    # ------------------------------------------------------------------
    def _build_height_and_actions(self) -> None:
        with ui.column().classes("w-52 gap-3"):
            with ui.card().classes("items-center p-3 gap-2"):
                ui.label("Z height").classes("text-sm font-medium")
                self.z_slider = ui.slider(
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    value=self.state.z_height,
                    on_change=self._on_height_change,
                ).props("vertical reverse label-always")
            with ui.card().classes("p-3 gap-2"):
                ui.label("Quick sizes").classes("text-sm font-medium")
                with ui.row().classes("gap-2 flex-wrap"):
                    self._compact_button("A4", lambda: self._quick_size("A4"))
                    self._compact_button("A5", lambda: self._quick_size("A5"))
                    self._compact_button("15 cm", lambda: self._quick_size("15 cm"))
                    self._compact_button("10 cm", lambda: self._quick_size("10 cm"))

    # ------------------------------------------------------------------
    # Pot controls
    # ------------------------------------------------------------------
    def _build_pot_controls(self) -> None:
        with ui.card().classes("flex-1 min-w-[320px] p-3 gap-3"):
            ui.label("Color pots").classes("text-sm font-medium")
            with ui.row().classes("gap-2 flex-wrap items-center"):
                self._compact_button("+ Pot", self._add_pot, color="primary")
                self._compact_button("Delete", self._remove_pot, color="negative")
                self.color_picker = ui.color_input(value="#3a86ff", on_change=self._on_color_change).props(
                    "label='Pot color' dense"
                )
                self.color_picker.disable()
            self.pot_select = ui.select(
                options=[],
                value=None,
                with_input=False,
                on_change=self._on_pot_selected,
            ).props("label='Pot selection' dense")
            ui.label("Pots appear as overlay circles with their configured colors.").classes("text-xs text-gray-500")

    # ------------------------------------------------------------------
    # Runner panel
    # ------------------------------------------------------------------
    def _build_runner_panel(self) -> None:
        with ui.card().classes("p-3 gap-3"):
            ui.label("Renderer configuration").classes("text-sm font-medium")
            ui.toggle(options=["start", "centroid", "per_segment", "threshold"], value="per_segment").props(
                "type=button unelevated toggle-color=primary label='z_mode'"
            )
            with ui.row().classes("gap-3"):
                ui.number(label="z_threshold", value=0.02, min=0.0, max=1000.0, step=0.01)
                ui.number(label="lift_delta", value=0.2, min=0.0, max=1.0, step=0.01)
            with ui.row().classes("gap-3"):
                ui.number(label="settle_down_s", value=0.05, min=0.0, max=5.0, step=0.01)
                ui.number(label="settle_up_s", value=0.03, min=0.0, max=5.0, step=0.01)
            with ui.row().classes("gap-3"):
                ui.number(label="z_step", value=0.1, min=0.0, max=1.0, step=0.01)
                ui.number(label="z_step_delay", value=0.03, min=0.0, max=1.0, step=0.005)
            with ui.row().classes("gap-3"):
                ui.number(label="flush_every", value=200, min=1, step=10)
                ui.number(label="feed_travel", value=15000, min=1, step=100)

            ui.separator()
            ui.label("Run options").classes("text-sm font-medium")
            with ui.row().classes("gap-3"):
                ui.number(label="start_x", value=0.0, min=0.0, step=0.1)
                ui.number(label="start_y", value=0.0, min=0.0, step=0.1)
                ui.checkbox("optimize nn")
            with ui.row().classes("gap-3"):
                ui.checkbox("combine endpoints", value=True)
                ui.number(label="join_tol", value=0.05, min=0.0, step=0.01)
            with ui.row().classes("gap-3"):
                ui.checkbox("resample", value=True)
                ui.number(label="max_dev", value=0.1, min=0.0, step=0.01)
                ui.input(label="max_seg (mm)")

            ui.separator()
            ui.label("Pen filter").classes("text-base font-medium")
            ui.select(options=[], with_input=False, multiple=True).props(
                "hint='Populated after preview' label='Pens'"
            )

            with ui.row().classes("gap-3"):
                ui.button("Preview in overlay", color="info", on_click=lambda: self._notify("Preview requested."))
                ui.button("Start", color="positive", on_click=lambda: self._notify("Run started."))
                ui.button("Pause", on_click=lambda: self._notify("Run paused/resumed."))
                ui.button("Stop", color="negative", on_click=lambda: self._notify("Run stopped."))

            self.progress = ui.linear_progress(value=0.0).props("color=primary")
            self.progress_label = ui.label("Idle")

    # ------------------------------------------------------------------
    # Status area
    # ------------------------------------------------------------------
    def _build_status_area(self) -> None:
        with ui.card().classes("w-full p-3 gap-2"):
            ui.label("Selection").classes("text-sm font-medium")
            self.selection_label = ui.label("").classes("text-xs")
            ui.label("Status").classes("text-sm font-medium")
            self.status_log = ui.log(max_lines=200).classes("text-xs")
            for line in self.state.status_lines:
                self.status_log.push(line)
        self._update_selection_label()

    # ------------------------------------------------------------------
    # Event callbacks
    # ------------------------------------------------------------------
    def _notify(self, message: str) -> None:
        self._log_status(message)

    def _log_status(self, message: str) -> None:
        self.state.log(message)
        if self.status_log is not None:
            self.status_log.push(message)
        self._update_status_panels()

    def _update_status_panels(self) -> None:
        if self.status_summary is not None:
            device = self.state.serial_device or "COM3"
            x, y, z = self._current_position()
            progress_text = "Idle"
            if self.progress is not None and self.progress.value and self.progress.value > 0.0:
                progress_text = f"Running ({self.progress.value * 100:.0f}%)"
            summary = (
                f"Connected | {device} @ 115200 | X={x:.1f} | Y={y:.1f} | Z={z:.2f} | {progress_text}"
            )
            self.status_summary.set_text(summary)
        if self.recent_status_container is not None:
            self.recent_status_container.clear()
            recent = self.state.status_lines[-3:]
            with self.recent_status_container:
                for entry in recent:
                    ui.label(entry).classes("text-xs text-gray-700")

    def _current_position(self) -> Tuple[float, float, float]:
        entity = self.state.selected_entity
        if entity:
            kind, key = entity
            if kind == "corner" and key in {"BL", "BR", "TL", "TR"}:
                x, y = self._corner_world_coords(key)
                z = self.state.corner_heights.get(str(key), self.state.z_height)
                return x, y, z
            if kind == "pot":
                pot = next((p for p in self.state.pots if p.identifier == key), None)
                if pot:
                    x, y = pot.position
                    return x, y, pot.height
        return 0.0, 0.0, self.state.z_height

    def _update_area_label(self) -> None:
        if self.area_label is None:
            return
        min_x, min_y = self.state.rect_min
        max_x, max_y = self.state.rect_max
        width = max(0.0, max_x - min_x)
        height = max(0.0, max_y - min_y)
        self.area_label.text = f"Work area: {width:.1f} × {height:.1f} mm"

    def _update_selection_label(self) -> None:
        if self.selection_label is None:
            return
        entity = self.state.selected_entity
        if not entity:
            self.selection_label.text = "No selection"
            return
        kind, key = entity
        if kind == "corner":
            corner_key = str(key)
            x, y = self._corner_world_coords(corner_key)
            z = self.state.corner_heights[corner_key]
            self.selection_label.text = f"Corner {corner_key}: ({x:.1f}, {y:.1f}) | Z {z:.2f}"
        elif kind == "pot":
            pot = next((p for p in self.state.pots if p.identifier == int(key)), None)
            if pot is None:
                self.selection_label.text = "No pot selected"
                return
            x, y = pot.position
            self.selection_label.text = (
                f"Pot #{pot.identifier}: ({x:.1f}, {y:.1f}) | Z {pot.height:.2f} | {pot.color}"
            )
        else:
            self.selection_label.text = "No selection"
        self._update_status_panels()

    def _on_workpiece_change(self, e: events.ValueChangeEventArguments) -> None:
        self.state.workpiece = e.value
        self._notify(f"Workpiece changed to {e.value}.")

    def _on_height_change(self, e: events.ValueChangeEventArguments) -> None:
        if self._suppress_height_event:
            return
        height = float(e.value)
        self.state.z_height = height
        entity = self.state.selected_entity
        if entity:
            kind, key = entity
            if kind == "corner":
                corner_key = str(key)
                self.state.corner_heights[corner_key] = height
                self._log_status(f"Set corner {corner_key} height to {height:.2f}.")
            elif kind == "pot":
                pot = next((p for p in self.state.pots if p.identifier == int(key)), None)
                if pot:
                    pot.height = height
                    self._log_status(f"Set pot #{pot.identifier} height to {height:.2f}.")
                    self._refresh_pots(selected_id=pot.identifier)
        self._update_selection_label()
        self._update_canvas()

    def _quick_size(self, size: str) -> None:
        self.state.workpiece = size
        if self.workpiece_select is not None:
            self.workpiece_select.value = size
        presets = {
            "A4": (297.0, 210.0),
            "A5": (210.0, 148.0),
            "15 cm": (150.0, 150.0),
            "10 cm": (100.0, 100.0),
        }
        width, height = presets.get(size, (200.0, 200.0))
        width = min(width, self.state.bed_width)
        height = min(height, self.state.bed_height)
        min_x = max(0.0, (self.state.bed_width - width) / 2)
        min_y = 0.0
        self.state.rect_min = (min_x, min_y)
        self.state.rect_max = (min_x + width, min_y + height)
        self._update_canvas()
        self._update_selection_label()
        self._log_status(f"Configured work area preset: {size} ({width:.0f} × {height:.0f} mm).")

    def _reset_all_z_heights(self) -> None:
        for key in self.state.corner_heights:
            self.state.corner_heights[key] = 1.0
        for pot in self.state.pots:
            pot.height = 1.0
        self.state.z_height = 1.0
        if self.z_slider is not None:
            try:
                self._suppress_height_event = True
                self.z_slider.value = 1.0
            finally:
                self._suppress_height_event = False
        self._update_selection_label()
        self._update_canvas()
        self._log_status("Reset all Z heights to 1.0")

    def _add_pot(self) -> None:
        base_color = "#3a86ff"
        if self.color_picker is not None and self.color_picker.value:
            base_color = self.color_picker.value
        pot_position = self._default_pot_position()
        pot = Pot(
            identifier=self.state.next_pot_id,
            color=base_color,
            height=self.state.z_height,
            position=pot_position,
        )
        self.state.next_pot_id += 1
        self.state.pots.append(pot)
        self.state.selected_pot_id = pot.identifier
        self._refresh_pots(selected_id=pot.identifier)
        self._select_entity(("pot", pot.identifier))
        self._log_status(f"Added pot #{pot.identifier} at ({pot.position[0]:.1f}, {pot.position[1]:.1f}).")

    def _remove_pot(self) -> None:
        if not self.state.pots:
            self._notify("No pots to delete.")
            return
        removed = self.state.pots.pop()
        if self.state.selected_pot_id == removed.identifier:
            self.state.selected_pot_id = self.state.pots[-1].identifier if self.state.pots else None
        self._refresh_pots()
        if self.state.selected_pot_id is not None:
            self._select_entity(("pot", self.state.selected_pot_id))
        else:
            self._select_entity(("corner", "BL"))
        self._log_status(f"Deleted pot #{removed.identifier}.")

    def _on_color_change(self, e: events.ValueChangeEventArguments) -> None:
        if self._suppress_color_event:
            return
        if not self.state.pots or self.state.selected_pot_id is None:
            return
        pot = next((p for p in self.state.pots if p.identifier == self.state.selected_pot_id), None)
        if pot is None:
            return
        pot.color = e.value
        self._refresh_pots(selected_id=pot.identifier)
        self._log_status(f"Updated pot #{pot.identifier} color to {e.value}.")

    def _on_pot_selected(self, e: events.ValueChangeEventArguments) -> None:
        if self._suppress_pot_event:
            return
        if e.value is None:
            return
        try:
            pot_id = int(e.value)
        except (TypeError, ValueError):
            self._log_status("Invalid pot selection.")
            return
        pot = next((p for p in self.state.pots if p.identifier == pot_id), None)
        if pot is None:
            self._log_status("Pot selection cleared.")
            return
        self._select_entity(("pot", pot_id))
        self._log_status(f"Selected pot #{pot.identifier}.")

    def _refresh_pots(self, selected_id: Optional[int] = None) -> None:
        if self.pot_select is None:
            return
        options = [
            {
                "label": f"Pot #{p.identifier} (Z {p.height:.2f})",
                "value": str(p.identifier),
            }
            for p in self.state.pots
        ]
        self.pot_select.options = options
        valid_ids = {p.identifier for p in self.state.pots}
        if selected_id is not None:
            self.state.selected_pot_id = selected_id
        if self.state.selected_pot_id not in valid_ids:
            self.state.selected_pot_id = None
        target_value = (
            str(self.state.selected_pot_id) if self.state.selected_pot_id in valid_ids else None
        )
        if self.pot_select.value != target_value:
            try:
                self._suppress_pot_event = True
                self.pot_select.value = target_value
            finally:
                self._suppress_pot_event = False
        self._update_canvas()


def _parse_bed_size(value: str) -> Tuple[float, float]:
    raw = value.strip().lower().replace("mm", "")
    try:
        width_str, height_str = raw.split("x", 1)
        width = float(width_str)
        height = float(height_str)
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("Area size must be in WIDTHxHEIGHT format, e.g. 300x245") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Area dimensions must be positive numbers.")
    return width, height


@ui.page("/")
def main_page() -> None:
    """Instantiate and render the plotter application for the active client."""
    serial_device = APP_CONFIG.get("serial_device")
    bed_size = APP_CONFIG.get("bed_size", DEFAULT_BED_SIZE)
    if not isinstance(bed_size, tuple) or len(bed_size) != 2:
        bed_size = DEFAULT_BED_SIZE
    plotter_app = PlotterApp(serial_device=serial_device, bed_size=bed_size)  # type: ignore[arg-type]
    plotter_app.create()


if __name__ in {"__main__", "__mp_main__"}:
    parser = argparse.ArgumentParser(description="Launch the Pen Plotter Control Suite UI.")
    parser.add_argument(
        "--serial-device",
        "-s",
        dest="serial_device",
        help="Path to the pen plotter serial device (e.g. /dev/tty.usbserial-A50285BI).",
    )
    parser.add_argument(
        "--area-size",
        "-a",
        dest="bed_size",
        type=_parse_bed_size,
        default=DEFAULT_BED_SIZE,
        metavar="WIDTHxHEIGHT",
        help="Maximum bed size in millimeters (width x height) to display, e.g. 300x245.",
    )
    args = parser.parse_args()

    APP_CONFIG["serial_device"] = args.serial_device
    APP_CONFIG["bed_size"] = args.bed_size

    cache_dir = Path(__file__).resolve().parent / ".matplotlib_cache"
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    cache_dir.mkdir(parents=True, exist_ok=True)

    ui.run(title="Pen Plotter Control Suite", show=False, reload=False)
