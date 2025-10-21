# Pen Plotter Control Suite

Modern tooling for experimenting with pen plotter hardware, pattern generation, and GRBL-based execution.

![Pen Plotter UI Placeholder](docs/images/ui.png)

## Overview

This repository brings together three complementary pieces:

- A NiceGUI application (`nicegui_app.py`) that recreates the original pen plotter widget, including bed visualization, pot calibration, serial connectivity, and live status logging.
- A lightweight plotting engine (`pattern.py` and `penplot_helper.py`) that models strokes, optimizes tool paths, and streams commands to GRBL-compatible devices.
- A library of generative-art notebooks and exported SVGs (`patterns/`) that demonstrate techniques for producing plot-ready artwork.

Use it as a standalone control surface for your hardware, a sandbox for experimenting with renders, or a portfolio of plotting experiments.

## Features

- **Interactive UI:** Configure bed dimensions, manage sampling pots, preview stroke order, and monitor GRBL traffic from a browser.
- **Hardware integration:** Minimal yet robust wrapper around GRBL that handles connection management, safe moves, servo calibration, and bilinear pen-height compensation.
- **Pattern toolkit:** Build patterns out of polylines, circles, and lines; resample segments; automatically optimize travel order; and preview pen assignments.
- **Generative notebooks:** Reproducible Jupyter notebooks for physics-inspired motion, ray tracing, handwriting synthesis, moiré studies, and more—each exporting SVGs suitable for plotting.
- **Extensible architecture:** Renderer hooks, optional widget APIs, and a modular state container make it easy to introduce new controls or plotting behaviors.

## Repository Layout

- `nicegui_app.py` – NiceGUI front end for configuring plots, visualizing bed geometry, and talking to GRBL.
- `penplot_helper.py` – Communication helpers, safety checks, and pen-height compensation utilities.
- `pattern.py` – Core geometry primitives plus the `Renderer` that streams drawing paths.
- `patterns/` – Notebook sources (`*.ipynb`) and exported SVG assets grouped by experiment.
- `requirements*.txt` – Dependency snapshots for the GUI core and the optional notebook stack.

## Prerequisites

- Python 3.10 or newer (NiceGUI 1.4+ and modern scientific tooling are tested against 3.10/3.11).
- Node.js is **not** required; NiceGUI bundles its own frontend assets.
- Hardware: a GRBL-compatible pen plotter and USB serial access (optional—app can run in mock/demo mode).

## Environment Setup

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Install the optional scientific stack when you want to execute the notebooks:

```bash
pip install -r requirements-notebooks.txt
```

If you intend to use PyTorch handwriting synthesis (`patterns/pytorch-handwriting-synthesis-toolkit/`), follow that submodule's README for the additional dependencies.

## Running the NiceGUI App

Launch the UI directly from the project root:

```bash
python nicegui_app.py \
  --serial-device /dev/tty.usbserial-A50285BI \
  --area-size 300x245
```

Key options:

- `--serial-device` – USB device path for your plotter (leave unset to explore the interface without hardware).
- `--area-size` – Maximum bed size in millimeters, formatted as `WIDTHxHEIGHT`.

When the server starts, open the printed `http://127.0.0.1:port` link in your browser. The UI runs entirely client-side once loaded, so you can keep it up while iterating on patterns or calibration data.

### Core Workflows in the UI

- **Bed alignment:** Drag the rectangle corners to match your plotting surface; heights feed into bilinear compensation.
- **Pot management:** Add sampling pots, set their heights, and assign pens to specific paths for multi-color sequences.
- **Pattern preview:** Load SVGs, toggle overlays, and inspect travel order before committing to a run.
- **Serial console:** Connect to GRBL, send manual G-code, and tail recent device responses.
- **Run control:** Start, pause, resume, or cancel the active render while keeping the status log for troubleshooting.

## Working with Patterns

The `pattern.py` primitives and renderer are usable both inside notebooks and programmatically:

```python
from pattern import Pattern, Circle, Renderer
from penplot_helper import GRBL, Config

pattern = Pattern().add(Circle((150.0, 120.0), r=80.0, pen_id=1))

grbl = GRBL(Config(port='/dev/tty.usbserial-A50285BI')).connect()
renderer = Renderer(grbl, optimize='nn')
renderer.run(pattern, preview_in_widget=True)
```

Notebooks under `patterns/` demonstrate more advanced workflows:

- `04_doublePendulum.ipynb` – Chaotic motion studies exported as clean polylines.
- `05_raytracing.ipynb` – Analytic ray-trace visualizations rendered to SVG.
- `06_handwriting.ipynb` – Vectorized handwriting synthesis leveraging `svgpathtools` and Matplotlib.

Each notebook writes SVGs into its subdirectory. Feed those into the UI via the pattern loader or straight to the renderer.

## Development Notes

- **Matplotlib cache:** The app sets `MPLCONFIGDIR` to `.matplotlib_cache/` to avoid permission issues when running in fresh environments.
- **Hot reload:** NiceGUI runs with `reload=True` by default, so UI changes take effect without restarting.
- **Safety:** `penplot_helper.GRBL` clamps moves to the configured bed, enforces pen-up travel, and exposes pause/cancel controls. Always test new tool paths with the pen lifted.

## Troubleshooting & Tips

- Serial ports on macOS typically appear under `/dev/tty.usb*`; on Linux they are usually `/dev/ttyUSB*`. Use `python -m serial.tools.list_ports` to discover devices.
- If NiceGUI warns about missing dependencies, make sure the virtual environment is active before installing requirements.
- Broken SVG imports are often due to unsupported path commands; preprocess them in the notebooks or via `pattern.Pattern.add`.

## License

Document the license terms that apply to your project here (for example, MIT, Apache 2.0, or proprietary). Update this section before publishing.
