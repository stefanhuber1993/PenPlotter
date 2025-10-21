# Pen Plotter Control Suite

Modern tooling for experimenting with pen plotter hardware, pattern generation, and GRBL-based execution.

> Replace `docs/images/ui.png` with an actual screenshot of the NiceGUI interface when you have one.

![Pen Plotter UI Placeholder](docs/images/ui.png)

## Overview

The project bundles three layers that cover design, preview, and execution:

- `nicegui_app.py` – Browser UI for configuring the bed, calibrating pots, managing runs, and monitoring GRBL.
- `pattern.py` / `penplot_helper.py` – Geometry primitives plus a renderer that streams clean tool paths.
- `patterns/` – Generative notebooks and exported SVGs that double as a plotting portfolio.

Run the GUI to drive hardware, import SVG artwork, or adapt the backend in your own scripts.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt
```

Notebook tooling (optional):

```bash
pip install -r requirements-notebooks.txt
```

Python 3.10+ is recommended. The app works without a connected plotter, so you can explore the UI before bringing hardware online.

## Run the GUI

```bash
python nicegui_app.py \
  --area-size 300x245
```

Open the printed `http://127.0.0.1:PORT` link in your browser.

**Typical workflow**
- Drop SVG files onto the `Load` tab to queue them for plotting — this is the default path for artwork produced by the notebooks.
- Alternatively, paste pattern text into the load field and set feed and pen overrides inline when you are iterating on custom geometry.
- Use the bed controls to align the compensation rectangle and corner heights before running.
- Connect through the serial tab to jog, send G-code, and monitor GRBL responses.

## Programmatic Use

The backend runs headless when you want to drive a plotter from Python:

```python
from pattern import Pattern, Circle, Renderer
from penplot_helper import GRBL, Config

pattern = Pattern().add(Circle((150.0, 120.0), r=80.0, pen_id=1))

grbl = GRBL(Config(port='/dev/tty.usbserial-A50285BI')).connect()
renderer = Renderer(grbl, optimize='nn')
renderer.run(pattern)
```

Apply helpers such as `Pattern.optimize_order_nn`, `resample_polylines`, and `combine_endpoints` to clean up geometry before execution.

## Patterns & Notebooks

Notebooks in `patterns/` explore double pendulums, ray tracing, handwriting synthesis, moiré fields, and more. Each notebook exports SVGs into its folder; load them into the GUI or stream them directly with the renderer. If you dive into `patterns/pytorch-handwriting-synthesis-toolkit/`, follow that subproject’s instructions for extra dependencies.

## Troubleshooting

- macOS serial ports appear under `/dev/tty.usb*`; Linux typically uses `/dev/ttyUSB*`. Run `python -m serial.tools.list_ports` to discover devices.
- The GUI sets `MPLCONFIGDIR` to `.matplotlib_cache/` to avoid permission issues; keep the directory writable.
- Always validate new tool paths with the pen lifted and keep the pause/cancel controls handy for untrusted SVGs.

## License

Document the license terms that apply to your project here (for example, MIT, Apache 2.0, or proprietary). Update this section before publishing.
