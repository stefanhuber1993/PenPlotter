"""Standalone NiceGUI application that recreates the existing pen plotter widget UI.

Run with:
    python nicegui_app.py
and open the reported URL in a browser.
"""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

from nicegui import events, ui


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

    def log(self, message: str) -> None:
        self.status_lines.append(message)
        if len(self.status_lines) > 200:
            del self.status_lines[: len(self.status_lines) - 200]


class PlotterApp:
    """Encapsulates layout creation and interactions for the NiceGUI app."""

    def __init__(self) -> None:
        self.state = PlotterState()
        self.status_log = None
        self.pot_select = None
        self.progress = None
        self.progress_label = None
        self.workpiece_select = None
        self.color_picker = None

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------
    def create(self) -> None:
        with ui.header().classes("items-center justify-between bg-primary text-white"):
            ui.label("Pen Plotter Control Suite").classes("text-xl font-semibold")
            self.workpiece_select = ui.select(
                ["A4", "A5", "15 cm", "10 cm"],
                value=self.state.workpiece,
                on_change=self._on_workpiece_change,
            ).props("label='Workpiece'")

        with ui.column().classes("w-full max-w-screen-xl mx-auto p-6 gap-6"):
            ui.label("Area setup, Z compensation, color pots, overlay preview, and run controls.").classes(
                "text-lg text-gray-600"
            )
            with ui.row().classes("w-full items-start gap-6"):
                self._build_canvas_section()
                self._build_height_and_actions()
            with ui.row().classes("w-full items-start gap-6"):
                self._build_pot_controls()
                self._build_runner_panel()
            self._build_status_area()

    # ------------------------------------------------------------------
    # Canvas and overlay mock
    # ------------------------------------------------------------------
    def _build_canvas_section(self) -> None:
        with ui.card().classes("flex-1 min-w-[320px]"):
            ui.label("Plotting bed").classes("text-base font-medium")
            grid_svg = """
            <svg viewBox="0 0 620 420" width="620" height="420">
              <rect x="0" y="0" width="620" height="420" fill="#f9fafb" stroke="#e5e7eb" stroke-width="2" rx="12" />
              <g stroke="#d1d5db" stroke-width="1">
                {vertical_lines}
                {horizontal_lines}
              </g>
              <rect x="20" y="20" width="580" height="380" fill="none" stroke="#4b5563" stroke-width="2" />
            </svg>
            """
            v_lines = "\n".join(
                f'<line x1="{x}" y1="20" x2="{x}" y2="400" />' for x in range(20, 601, 50)
            )
            h_lines = "\n".join(
                f'<line x1="20" y1="{y}" x2="600" y2="{y}" />' for y in range(20, 401, 50)
            )
            ui.html(
                content=grid_svg.format(vertical_lines=v_lines, horizontal_lines=h_lines),
                sanitize=False,
            ).classes("border rounded-lg")
            with ui.row().classes("mt-4 gap-4"):
                ui.button("Hide Overlay", on_click=lambda: self._notify("Toggled overlay."), color="warning")
                ui.button("Delete Plot", on_click=lambda: self._notify("Cleared overlay."), color="negative")
                ui.button("Save & Apply", on_click=lambda: self._notify("Saved configuration."), color="positive")

    # ------------------------------------------------------------------
    # Height slider and primary actions
    # ------------------------------------------------------------------
    def _build_height_and_actions(self) -> None:
        with ui.column().classes("w-64 gap-4"):
            with ui.card().classes("items-center p-4"):
                ui.label("Z height").classes("font-medium")
                ui.slider(
                    min=0.0,
                    max=1.0,
                    step=0.01,
                    value=self.state.z_height,
                    on_change=self._on_height_change,
                ).props("vertical invert label-always")
            with ui.card().classes("p-4 gap-3"):
                ui.label("Jog & Calibration").classes("font-medium")
                ui.button("Sweep Rectangle", on_click=lambda: self._notify("Swept rectangle."), color="primary")
                ui.button("Pen Up (1.0)", on_click=lambda: self._notify("Moved pen up."))
                ui.button("Pen Down (0.0)", on_click=lambda: self._notify("Moved pen down."))
                ui.button("Home (0,0)", on_click=lambda: self._notify("Homed axes."))
            with ui.card().classes("p-4 gap-3"):
                ui.label("Quick sizes").classes("font-medium")
                ui.button("A4", on_click=lambda: self._quick_size("A4"))
                ui.button("A5", on_click=lambda: self._quick_size("A5"))
                ui.button("15 cm", on_click=lambda: self._quick_size("15 cm"))
                ui.button("10 cm", on_click=lambda: self._quick_size("10 cm"))

    # ------------------------------------------------------------------
    # Pot controls
    # ------------------------------------------------------------------
    def _build_pot_controls(self) -> None:
        with ui.card().classes("flex-1 min-w-[320px] p-4 gap-4"):
            ui.label("Color pots").classes("text-base font-medium")
            with ui.row().classes("gap-3"):
                ui.button("+ Pot", on_click=self._add_pot, color="primary")
                ui.button("Delete Pot", on_click=self._remove_pot, color="negative")
                self.color_picker = ui.color_input(value="#3a86ff", on_change=self._on_color_change).props(
                    "label='Pot color'"
                )
            self.pot_select = ui.select(
                options=[],
                value=None,
                with_input=False,
                on_change=self._on_pot_selected,
            ).props("label='Pot selection'")
            ui.label("Pots appear as overlay circles with their configured colors.").classes("text-sm text-gray-500")

    # ------------------------------------------------------------------
    # Runner panel
    # ------------------------------------------------------------------
    def _build_runner_panel(self) -> None:
        with ui.card().classes("flex-1 min-w-[360px] p-4 gap-4"):
            ui.label("Renderer configuration").classes("text-base font-medium")
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
            ui.label("Run options").classes("text-base font-medium")
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
        with ui.card().classes("w-full p-4 gap-4"):
            ui.label("Selection").classes("text-base font-medium")
            self.selection_label = ui.label("Corner BL")
            ui.label("Status").classes("text-base font-medium")
            self.status_log = ui.log(max_lines=200)
            for line in self.state.status_lines:
                self.status_log.push(line)

    # ------------------------------------------------------------------
    # Event callbacks
    # ------------------------------------------------------------------
    def _notify(self, message: str) -> None:
        self.state.log(message)
        ui.notify(message)
        if self.status_log is not None:
            self.status_log.push(message)

    def _on_workpiece_change(self, e: events.ValueChangeEventArguments) -> None:
        self.state.workpiece = e.value
        self._notify(f"Workpiece changed to {e.value}.")

    def _on_height_change(self, e: events.ValueChangeEventArguments) -> None:
        self.state.z_height = e.value
        self.selection_label.text = f"Selected height: {e.value:.2f}"
        self._notify(f"Adjusted Z height to {e.value:.2f}.")

    def _quick_size(self, size: str) -> None:
        self.state.workpiece = size
        if self.workpiece_select is not None:
            self.workpiece_select.value = size
        self._notify(f"Configured quick size preset: {size}.")

    def _add_pot(self) -> None:
        base_color = "#3a86ff"
        if self.color_picker is not None and self.color_picker.value:
            base_color = self.color_picker.value
        pot = Pot(identifier=self.state.next_pot_id, color=base_color)
        self.state.next_pot_id += 1
        self.state.pots.append(pot)
        self.state.selected_pot_id = pot.identifier
        self._refresh_pots()
        self._notify(f"Added pot #{pot.identifier}.")

    def _remove_pot(self) -> None:
        if not self.state.pots:
            self._notify("No pots to delete.")
            return
        removed = self.state.pots.pop()
        if self.state.selected_pot_id == removed.identifier:
            self.state.selected_pot_id = self.state.pots[-1].identifier if self.state.pots else None
        self._refresh_pots()
        self._notify(f"Deleted pot #{removed.identifier}.")

    def _on_color_change(self, e: events.ValueChangeEventArguments) -> None:
        if not self.state.pots or self.state.selected_pot_id is None:
            return
        pot = next((p for p in self.state.pots if p.identifier == self.state.selected_pot_id), None)
        if pot is None:
            return
        pot.color = e.value
        self._refresh_pots()
        self._notify(f"Updated pot #{pot.identifier} color to {e.value}.")

    def _on_pot_selected(self, e: events.ValueChangeEventArguments) -> None:
        if e.value is None:
            return
        try:
            pot_id = int(e.value)
        except (TypeError, ValueError):
            self._notify("Invalid pot selection.")
            return
        self.state.selected_pot_id = pot_id
        pot = next((p for p in self.state.pots if p.identifier == pot_id), None)
        if pot:
            self.selection_label.text = f"Selected pot #{pot.identifier} ({pot.color})"
            if self.color_picker is not None:
                self.color_picker.value = pot.color
            self._notify(f"Selected pot #{pot.identifier}.")
        else:
            self.selection_label.text = "No pot selected"
            self._notify("Pot selection cleared.")

    def _refresh_pots(self) -> None:
        if self.pot_select is None:
            return
        options = [
            {"label": f"Pot #{p.identifier}", "value": str(p.identifier)} for p in self.state.pots
        ]
        self.pot_select.options = options
        if self.state.pots:
            selected_id = self.state.selected_pot_id or self.state.pots[-1].identifier
            self.state.selected_pot_id = selected_id
            self.pot_select.value = str(selected_id)
            pot = next((p for p in self.state.pots if p.identifier == selected_id), None)
            if pot and self.color_picker is not None:
                self.color_picker.value = pot.color
            if pot:
                self.selection_label.text = f"Selected pot #{pot.identifier} ({pot.color})"
        else:
            self.pot_select.value = None
            self.state.selected_pot_id = None
            self.selection_label.text = "No pot selected"


def main_page() -> None:
    """Instantiate and render the plotter application for the active client."""
    plotter_app = PlotterApp()
    plotter_app.create()


if __name__ in {"__main__", "__mp_main__"}:
    cache_dir = Path(__file__).resolve().parent / ".matplotlib_cache"
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    cache_dir.mkdir(parents=True, exist_ok=True)

    ui.page("/")(main_page)

    ui.run(title="Pen Plotter Control Suite", show=False)
