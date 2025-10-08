"""NiceGUI + FastAPI application for controlling a pen plotter."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import replace
from typing import Dict, List, Optional

from nicegui import app, events, ui

from .config import AppState, PenConfig, PlotterSettings
from .grbl_controller import GRBLConnectionError, GRBLController
from .plot_manager import PlotJob, PlotManager
from .svg_loader import SVGDocument
from .toolpath import PenToolpath, Transform, fits_workspace, generate_toolpaths, transformed_bounds

# ---------------------------------------------------------------------------
# Global state shared between UI and backend
# ---------------------------------------------------------------------------
settings = PlotterSettings()
state = AppState(settings=settings)
controller = GRBLController(settings)

status_messages: List[str] = []
terminal_messages: List[str] = []
status_lock = threading.Lock()
progress_value = 0.0

current_document: Optional[SVGDocument] = None
current_toolpaths: List[PenToolpath] = []
current_transform = Transform()
document_bounds = (0.0, 0.0, 0.0, 0.0)
document_width = 0.0
document_height = 0.0
sample_tolerance = 0.5

# UI element references (populated in create_ui)
ports_select: Optional[ui.select] = None  # type: ignore[assignment]
connection_label: Optional[ui.label] = None  # type: ignore[assignment]
document_info_label: Optional[ui.label] = None  # type: ignore[assignment]
job_size_label: Optional[ui.label] = None  # type: ignore[assignment]
workspace_warning_label: Optional[ui.label] = None  # type: ignore[assignment]
preview_html: Optional[ui.html] = None  # type: ignore[assignment]
pen_container: Optional[ui.column] = None  # type: ignore[assignment]
width_input: Optional[ui.number] = None  # type: ignore[assignment]
offset_x_input: Optional[ui.number] = None  # type: ignore[assignment]
offset_y_input: Optional[ui.number] = None  # type: ignore[assignment]
rotation_slider: Optional[ui.slider] = None  # type: ignore[assignment]
sampling_slider: Optional[ui.slider] = None  # type: ignore[assignment]
progress_bar: Optional[ui.linear_progress] = None  # type: ignore[assignment]
status_area: Optional[ui.textarea] = None  # type: ignore[assignment]
terminal_area: Optional[ui.textarea] = None  # type: ignore[assignment]
workspace_width_input: Optional[ui.number] = None  # type: ignore[assignment]
workspace_height_input: Optional[ui.number] = None  # type: ignore[assignment]
pen_height_slider: Optional[ui.slider] = None  # type: ignore[assignment]
servo_up_input: Optional[ui.number] = None  # type: ignore[assignment]
servo_down_input: Optional[ui.number] = None  # type: ignore[assignment]

plot_manager: PlotManager  # will be initialised after helper definitions


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _append_status(message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    with status_lock:
        status_messages.append(f"[{timestamp}] {message}")


def _append_terminal(message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    with status_lock:
        terminal_messages.append(f"[{timestamp}] {message}")


def _set_progress(value: float) -> None:
    global progress_value
    progress_value = max(0.0, min(1.0, value))


plot_manager = PlotManager(
    controller,
    settings,
    status_cb=_append_status,
    progress_cb=_set_progress,
)


def _refresh_ports() -> None:
    state.available_ports = controller.enumerate_ports()
    if ports_select is not None:
        ports_select.options = state.available_ports
        if state.available_ports and state.connected_port not in state.available_ports:
            ports_select.value = state.available_ports[0]


def _sync_status_to_ui() -> None:
    if connection_label is not None:
        if controller.is_connected:
            connection_label.text = f"Connected: {controller.connected_port}"
        else:
            connection_label.text = "Disconnected"
    if status_area is not None:
        with status_lock:
            status_area.value = "\n".join(status_messages[-250:])
    if terminal_area is not None:
        with status_lock:
            terminal_area.value = "\n".join(terminal_messages[-250:])
    if progress_bar is not None:
        progress_bar.value = progress_value


def _render_preview() -> None:
    if preview_html is None:
        return
    workspace = settings.workspace
    svg = render_preview_svg(current_toolpaths, workspace)
    preview_html.content = svg

    if workspace_warning_label is not None:
        if not current_toolpaths:
            workspace_warning_label.text = "Load an SVG to preview the job."
            workspace_warning_label.style("color: #666;")
        else:
            bounds = transformed_bounds(current_toolpaths)
            if fits_workspace(bounds, workspace):
                workspace_warning_label.text = "Job fits within the workspace."
                workspace_warning_label.style("color: #16a34a;")
            else:
                workspace_warning_label.text = "Warning: job exceeds workspace bounds!"
                workspace_warning_label.style("color: #dc2626;")

    if job_size_label is not None:
        if not current_toolpaths:
            job_size_label.text = "Job size: n/a"
        else:
            xmin, xmax, ymin, ymax = transformed_bounds(current_toolpaths)
            job_size_label.text = (
                f"Job size: {max(0.0, xmax - xmin):.1f} × {max(0.0, ymax - ymin):.1f} mm"
            )


def _update_pen_controls() -> None:
    if pen_container is None:
        return
    pen_container.clear()
    if not state.pens:
        with pen_container:
            ui.label("Load an SVG to configure pens.").classes("text-sm text-gray-500")
        return
    for color, pen in sorted(state.pens.items(), key=lambda item: item[0]):
        with pen_container:
            with ui.row().classes("items-center gap-2"):
                ui.element("div").style(
                    f"width:16px;height:16px;border-radius:50%;background:{color};border:1px solid #555;"
                )
                ui.label(color).classes("text-xs text-gray-500")
                ui.input(value=pen.name, placeholder="Pen name", on_change=lambda e, c=color: _set_pen_name(c, e.value)).classes("w-32")
                ui.number(label="Feed (mm/min)", value=pen.feed_rate, min=100, step=100,
                         on_change=lambda e, c=color: _set_pen_feed(c, e.value)).props("dense").classes("w-36")
                ui.switch("Enabled", value=pen.enabled,
                          on_change=lambda e, c=color: _set_pen_enabled(c, e.value)).props("dense")


def _set_pen_name(color: str, value: str) -> None:
    pen = state.pens.get(color)
    if pen:
        pen.name = value or pen.name


def _set_pen_feed(color: str, value) -> None:
    pen = state.pens.get(color)
    if pen:
        try:
            pen.feed_rate = max(100, int(float(value)))
        except (TypeError, ValueError):
            pass
        _rebuild_toolpaths()


def _set_pen_enabled(color: str, value: bool) -> None:
    pen = state.pens.get(color)
    if pen:
        pen.enabled = bool(value)
        _rebuild_toolpaths()


def _rebuild_toolpaths() -> None:
    global current_toolpaths
    if current_document is None:
        current_toolpaths = []
        _render_preview()
        return
    try:
        current_toolpaths = generate_toolpaths(
            current_document,
            state.pens,
            current_transform,
            tolerance=sample_tolerance,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _append_status(f"Failed to generate toolpaths: {exc}")
        current_toolpaths = []
    _render_preview()


def _set_document(document: SVGDocument, name: str) -> None:
    global current_document, document_bounds, document_width, document_height, current_transform
    current_document = document
    state.current_document_name = name
    document_bounds = document.bounds()
    xmin, xmax, ymin, ymax = document_bounds
    document_width = max(0.0, xmax - xmin)
    document_height = max(0.0, ymax - ymin)

    current_transform = Transform(
        scale=1.0,
        rotation_deg=0.0,
        offset_x=0.0,
        offset_y=0.0,
        origin_x=xmin,
        origin_y=ymin,
    )

    _initialise_pen_configs(document)
    _auto_scale_to_workspace()

    if document_info_label is not None:
        document_info_label.text = (
            f"{name} — native size {document_width:.1f} × {document_height:.1f} units"
        )
    _append_status(f"Loaded SVG: {name}")
    _rebuild_toolpaths()


def _initialise_pen_configs(document: SVGDocument) -> None:
    previous = state.pens.copy()
    state.pens.clear()
    for index, color in enumerate(sorted(document.group_by_color().keys())):
        existing = previous.get(color)
        if existing:
            state.pens[color] = existing
        else:
            state.pens[color] = PenConfig(
                name=f"Pen {index + 1}",
                color=color,
                feed_rate=3000,
                enabled=True,
            )
    _update_pen_controls()


def _auto_scale_to_workspace() -> None:
    if width_input is None or offset_x_input is None or offset_y_input is None:
        return
    workspace = settings.workspace
    if document_width <= 0 or document_height <= 0:
        scale = 1.0
    else:
        scale = min(
            workspace.width_mm / document_width,
            workspace.height_mm / document_height,
        )
        if not math.isfinite(scale) or scale <= 0:
            scale = 1.0
    current_transform.scale = scale
    current_transform.rotation_deg = 0.0

    width_input.value = document_width * scale
    if rotation_slider is not None:
        rotation_slider.value = 0.0

    # center the job by default
    if current_document is not None and state.pens:
        tmp_transform = replace(current_transform, offset_x=0.0, offset_y=0.0)
        toolpaths = generate_toolpaths(
            current_document, state.pens, tmp_transform, tolerance=sample_tolerance
        )
        if toolpaths:
            bounds = transformed_bounds(toolpaths)
            job_w = bounds[1] - bounds[0]
            job_h = bounds[3] - bounds[2]
            current_transform.offset_x = (workspace.width_mm - job_w) / 2.0 - bounds[0]
            current_transform.offset_y = (workspace.height_mm - job_h) / 2.0 - bounds[2]
        else:
            current_transform.offset_x = 0.0
            current_transform.offset_y = 0.0
    else:
        current_transform.offset_x = 0.0
        current_transform.offset_y = 0.0

    offset_x_input.value = current_transform.offset_x
    offset_y_input.value = current_transform.offset_y


def _set_workspace(width: float, height: float) -> None:
    try:
        settings.workspace.width_mm = max(10.0, float(width))
        settings.workspace.height_mm = max(10.0, float(height))
    except (TypeError, ValueError):
        return
    _append_status(
        f"Workspace updated: {settings.workspace.width_mm:.1f} × {settings.workspace.height_mm:.1f} mm"
    )
    _rebuild_toolpaths()


def _update_transform_from_inputs() -> None:
    if width_input is None or rotation_slider is None or offset_x_input is None or offset_y_input is None:
        return
    if document_width <= 0:
        return
    try:
        desired_width = float(width_input.value or 0)
    except (TypeError, ValueError):
        desired_width = document_width
    scale = desired_width / document_width if document_width else 1.0
    if not math.isfinite(scale) or scale <= 0:
        scale = 1.0
    current_transform.scale = scale
    current_transform.rotation_deg = float(rotation_slider.value or 0)
    try:
        current_transform.offset_x = float(offset_x_input.value or 0)
        current_transform.offset_y = float(offset_y_input.value or 0)
    except (TypeError, ValueError):
        pass
    _rebuild_toolpaths()


def _auto_center() -> None:
    if current_document is None:
        return
    workspace = settings.workspace
    tmp_transform = replace(current_transform, offset_x=0.0, offset_y=0.0)
    toolpaths = generate_toolpaths(current_document, state.pens, tmp_transform, tolerance=sample_tolerance)
    bounds = transformed_bounds(toolpaths)
    job_w = bounds[1] - bounds[0]
    job_h = bounds[3] - bounds[2]
    if toolpaths:
        current_transform.offset_x = (workspace.width_mm - job_w) / 2.0 - bounds[0]
        current_transform.offset_y = (workspace.height_mm - job_h) / 2.0 - bounds[2]
    else:
        current_transform.offset_x = 0.0
        current_transform.offset_y = 0.0
    if offset_x_input is not None:
        offset_x_input.value = current_transform.offset_x
    if offset_y_input is not None:
        offset_y_input.value = current_transform.offset_y
    _rebuild_toolpaths()


def _handle_upload(event: events.UploadEventArguments) -> None:
    content = event.content.read()
    document = SVGDocument.from_bytes(content, name=event.name or "uploaded.svg")
    _set_document(document, event.name or "uploaded.svg")


def _connect_selected_port() -> None:
    if ports_select is None:
        return
    port = ports_select.value
    if not port:
        _append_status("Select a serial port before connecting.")
        return
    try:
        controller.connect(port)
        state.connected_port = port
        _append_status(f"Connected to {port}")
    except GRBLConnectionError as exc:
        _append_status(f"Connection failed: {exc}")


def _disconnect() -> None:
    controller.disconnect()
    state.connected_port = None
    _append_status("Disconnected")


def _send_manual_gcode(command: str) -> None:
    command = (command or "").strip()
    if not command:
        return
    if not controller.is_connected:
        _append_terminal("Cannot send command: device not connected")
        return
    try:
        responses = controller.send_command(command)
        _append_terminal(f"> {command}")
        for line in responses:
            _append_terminal(line)
    except GRBLConnectionError as exc:
        _append_terminal(f"Error: {exc}")


def _start_job(toolpaths=None) -> None:
    if not controller.is_connected:
        _append_status("Connect to the plotter before starting a job.")
        return
    if toolpaths is None:
        toolpaths = current_toolpaths
    if not toolpaths:
        _append_status("No toolpaths to plot. Ensure pens are enabled and SVG is loaded.")
        return
    try:
        plot_manager.start(PlotJob(toolpaths=toolpaths))
    except RuntimeError as exc:
        _append_status(str(exc))


def _pause_job() -> None:
    plot_manager.pause()


def _resume_job() -> None:
    plot_manager.resume()


def _stop_job() -> None:
    plot_manager.stop()


def _set_servo_up(value) -> None:
    try:
        settings.servo.up = int(value)
    except (TypeError, ValueError):
        return
    _append_status(f"Servo up PWM set to {settings.servo.up}")


def _set_servo_down(value) -> None:
    try:
        settings.servo.down = int(value)
    except (TypeError, ValueError):
        return
    _append_status(f"Servo down PWM set to {settings.servo.down}")


def _apply_pen_height(value: float) -> None:
    if not controller.is_connected:
        _append_status("Connect to the device before adjusting pen height.")
        return
    pwm = settings.servo.to_pwm(float(value))
    controller.send_command(f"M3 S{pwm}")
    _append_status(f"Pen height set to {value:.2f} (PWM {pwm})")


def _pen_up() -> None:
    if controller.is_connected:
        controller.pen_up()
        _append_status("Pen lifted")


def _pen_down() -> None:
    if controller.is_connected:
        controller.pen_down()
        _append_status("Pen lowered")


# ---------------------------------------------------------------------------
# UI construction
# ---------------------------------------------------------------------------

def create_ui() -> None:
    global ports_select, connection_label, document_info_label, job_size_label
    global workspace_warning_label, preview_html, pen_container, width_input
    global offset_x_input, offset_y_input, rotation_slider, sampling_slider
    global progress_bar, status_area, terminal_area, workspace_width_input
    global workspace_height_input, pen_height_slider, servo_up_input, servo_down_input

    ui.page_title("Pen Plotter Controller")
    ui.markdown("# Pen Plotter Controller")

    with ui.row().classes("w-full gap-6"):
        with ui.column().classes("w-1/3 gap-4"):
            # Connection card
            with ui.card().classes("w-full"):
                ui.label("Connection").classes("text-lg font-semibold")
                ports_select = ui.select(options=[], label="Serial port")
                ui.button("Refresh ports", on_click=_refresh_ports)
                with ui.row().classes("gap-2"):
                    ui.button("Connect", on_click=_connect_selected_port)
                    ui.button("Disconnect", on_click=_disconnect)
                connection_label = ui.label("Disconnected").classes("text-sm text-gray-500")

            # Workspace settings
            with ui.card():
                ui.label("Workspace").classes("text-lg font-semibold")
                workspace_width_input = ui.number(
                    label="Width (mm)", value=settings.workspace.width_mm, min=10, step=1,
                    on_change=lambda e: _set_workspace(e.value, workspace_height_input.value if workspace_height_input else settings.workspace.height_mm),
                )
                workspace_height_input = ui.number(
                    label="Height (mm)", value=settings.workspace.height_mm, min=10, step=1,
                    on_change=lambda e: _set_workspace(workspace_width_input.value if workspace_width_input else settings.workspace.width_mm, e.value),
                )

            # Pen configuration
            with ui.card():
                ui.label("Pens").classes("text-lg font-semibold")
                pen_container = ui.column().classes("gap-2")
                _update_pen_controls()

            # Pen height calibration
            with ui.card():
                ui.label("Pen height calibration").classes("text-lg font-semibold")
                servo_up_input = ui.number(
                    label="Pen up PWM", value=settings.servo.up, min=0, max=255,
                    on_change=lambda e: _set_servo_up(e.value),
                )
                servo_down_input = ui.number(
                    label="Pen down PWM", value=settings.servo.down, min=0, max=255,
                    on_change=lambda e: _set_servo_down(e.value),
                )
                pen_height_slider = ui.slider(
                    min=0.0, max=1.0, step=0.01, value=1.0,
                    on_change=lambda e: _apply_pen_height(e.value),
                ).props('label="Pen height"')
                with ui.row().classes("gap-2"):
                    ui.button("Pen up", on_click=_pen_up)
                    ui.button("Pen down", on_click=_pen_down)

            # Manual G-code terminal
            with ui.card():
                ui.label("G-code terminal").classes("text-lg font-semibold")
                gcode_input = ui.input(label="Command", placeholder="G1 X10 Y10")
                def _send_and_clear() -> None:
                    _send_manual_gcode(gcode_input.value or "")
                    gcode_input.value = ""

                ui.button("Send", on_click=_send_and_clear)
                terminal_area = ui.textarea(label="Terminal", value="", auto_resize=True)
                terminal_area.props("readonly")

        with ui.column().classes("w-2/3 gap-4"):
            # Document controls
            with ui.card():
                ui.label("Document").classes("text-lg font-semibold")
                ui.upload(label="Load SVG", auto_upload=True, on_upload=_handle_upload)
                document_info_label = ui.label("No document loaded").classes("text-sm text-gray-500")
                width_input = ui.number(
                    label="Output width (mm)", value=0.0, min=1, step=1, on_change=lambda e: _update_transform_from_inputs()
                )
                rotation_slider = ui.slider(
                    min=-180, max=180, value=0.0, step=1.0, on_change=lambda e: _update_transform_from_inputs()
                ).props('label="Rotation (deg)"')
                offset_x_input = ui.number(
                    label="Offset X (mm)", value=0.0, step=1.0, on_change=lambda e: _update_transform_from_inputs()
                )
                offset_y_input = ui.number(
                    label="Offset Y (mm)", value=0.0, step=1.0, on_change=lambda e: _update_transform_from_inputs()
                )
                sampling_slider = ui.slider(
                    min=0.1, max=5.0, value=sample_tolerance, step=0.1,
                    on_change=lambda e: _set_sampling_tolerance(e.value),
                ).props('label="Sampling tolerance (mm)"')
                ui.button("Auto center", on_click=_auto_center)

            # Preview
            with ui.card().classes("min-h-[320px]"):
                ui.label("Preview").classes("text-lg font-semibold")
                preview_html = ui.html(render_preview_svg([], settings.workspace)).classes("w-full h-72 bg-gray-900 rounded")
                workspace_warning_label = ui.label("Load an SVG to preview the job.")
                job_size_label = ui.label("Job size: n/a")

            # Job controls
            with ui.card():
                ui.label("Plotting").classes("text-lg font-semibold")
                with ui.row().classes("gap-2"):
                    ui.button("Start", on_click=lambda: _start_job())
                    ui.button("Pause", on_click=_pause_job)
                    ui.button("Resume", on_click=_resume_job)
                    ui.button("Stop", on_click=_stop_job)
                progress_bar = ui.linear_progress(value=0.0).classes("w-full")

            # Status log
            with ui.card():
                ui.label("Status log").classes("text-lg font-semibold")
                status_area = ui.textarea(value="", auto_resize=True)
                status_area.props("readonly")

    ui.timer(0.5, _sync_status_to_ui)
    _refresh_ports()
    _render_preview()


def _set_sampling_tolerance(value) -> None:
    global sample_tolerance
    try:
        sample_tolerance = max(0.1, float(value))
    except (TypeError, ValueError):
        return
    _rebuild_toolpaths()


def render_preview_svg(toolpaths, workspace) -> str:
    width = max(1.0, workspace.width_mm)
    height = max(1.0, workspace.height_mm)
    elements = [
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#111" stroke="#444" stroke-width="0.6" />'
    ]
    bounds = transformed_bounds(toolpaths) if toolpaths else (0.0, 0.0, 0.0, 0.0)
    xmin, xmax, ymin, ymax = bounds
    if toolpaths:
        job_rect = (
            f'<rect x="{xmin:.2f}" y="{height - ymax:.2f}" width="{max(0.0, xmax - xmin):.2f}" '
            f'height="{max(0.0, ymax - ymin):.2f}" fill="none" stroke="#888" stroke-dasharray="4 4" stroke-width="0.4" />'
        )
        elements.append(job_rect)
    for toolpath in toolpaths:
        stroke = toolpath.pen.color or "#ffffff"
        for poly in toolpath.polylines:
            if not poly:
                continue
            commands = [f"M {poly[0][0]:.2f} {height - poly[0][1]:.2f}"]
            for x, y in poly[1:]:
                commands.append(f"L {x:.2f} {height - y:.2f}")
            command_str = " ".join(commands)
            elements.append(
                f'<path d="{command_str}" fill="none" stroke="{stroke}" stroke-width="0.5" '
                f'stroke-linecap="round" stroke-linejoin="round" />'
            )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="xMidYMid meet">' + "".join(elements) + "</svg>"
    )
    return svg


@app.get("/api/status")
def api_status() -> Dict:
    return {
        "connected": controller.is_connected,
        "port": controller.connected_port,
        "job_state": plot_manager.state,
        "document": state.current_document_name,
        "progress": progress_value,
    }


def run(**kwargs) -> None:
    ui.run(**kwargs)


@ui.page("/")
def index() -> None:
    create_ui()
