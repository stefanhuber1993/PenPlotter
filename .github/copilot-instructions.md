# Copilot Instructions for PenPlotter

## Project Overview
This project provides a modular, beginner-friendly Python workflow for controlling a GRBL-based pen plotter. The codebase is organized for interactive use (Jupyter notebooks) and script-based automation, with a focus on safe, reliable hardware control and extensibility.

## Key Components
- `penplot_helper.py`: Core GRBL communication, device configuration, and safety logic. Defines `Config`, `GRBL`, and compensation classes. All hardware I/O and low-level commands are here.
- `pattern.py`: Geometric primitives (`Line`, `Polyline`, `Circle`) and the `Pattern` class for grouping and optimizing drawing instructions. All shapes are converted to polylines for device execution.
- `penplot_widgets.py`: Jupyter/ipywidgets UI, rendering, and live device control. Contains `MotionWorker`, `UIRenderer`, and integration with `pattern.py` and `penplot_helper.py`.
- `pen_plotter.ipynb`: Quickstart notebook for device setup, calibration, and plotting workflows. Demonstrates typical usage and safe operation.

## Developer Workflows
- **Interactive Use:** Run `pen_plotter.ipynb` for step-by-step device setup, calibration, and plotting. Use `%autoreload` for live code updates.
- **Device Connection:** Always instantiate `Config` and `GRBL` from `penplot_helper.py` to connect to hardware. Example:
  ```python
  from penplot_helper import Config, GRBL
  cfg = Config(port="/dev/tty.usbserial-A50285BI")
  grbl = GRBL(cfg).connect()
  ```
- **Safety:** Always home the device manually before plotting. Use `grbl.pen_up()` and `grbl.pen_down()` to control the pen safely.
- **Pattern Creation:** Use `Pattern().add(...)` to build up drawing instructions. All shapes are converted to polylines on add.
- **UI/Live Control:** Use `penplot_widgets.py` for interactive plotting and device feedback in Jupyter.

## Project Conventions
- All device moves are absolute and clipped to the configured bed size by default.
- Pen height compensation is supported via the `Compensation` class.
- All drawing instructions are ultimately polylines; `Line` and `Circle` are converted on add.
- Device communication is synchronous and blocking for safety.
- Use `feed_draw` and `pen_pressure` to control drawing speed and pressure per shape.

## Examples
- See `pen_plotter.ipynb` for end-to-end usage, including device connection, bed sweep, and plotting.
- Example pattern:
  ```python
  from pattern import Pattern, Polyline
  pat = Pattern().add(Polyline([(0,0), (10,0), (10,10), (0,10), (0,0)]))
  ```

## Integration Points
- Hardware: Communicates with GRBL via serial (requires `pyserial`).
- UI: Uses `ipywidgets` and `ipycanvas` for interactive control.

## Troubleshooting
- If serial connection fails, check the port in `Config` and ensure `pyserial` is installed.
- Always use the provided methods for device control—do not send raw G-code directly.

---
For further details, see the docstrings in each module and the quickstart notebook.
