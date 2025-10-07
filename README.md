# PenPlotter

A modular toolkit for building, previewing, and running pen plotter jobs without
being tied to a Jupyter notebook.  The repository now ships with a browser-based
control surface backed by a FastAPI server and a clean Python package for
geometry, rendering, and device orchestration.

## Highlights

- **Python package** – `penplotter` exposes reusable building blocks such as
  `Pattern`, `Polyline`, and `PlotterController` so you can script art from any
  environment.
- **Browser control app** – Launch the FastAPI server to obtain a fully featured
  UI with live preview, manual jogging, pen controls, and job monitoring.
- **Hardware abstraction** – Use the GRBL driver for real devices or the mock
  plotter when developing without hardware connected.
- **Modern structure** – Legacy notebook tooling is preserved under `legacy/`
  while new code lives in a conventional package layout with scripts and
  examples.

## Quick start

### 1. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install fastapi uvicorn[standard] requests pyserial
```

> The mock backend works without `pyserial`, but install it if you plan to talk
to a real GRBL controller.

### 2. Run the control server

```bash
python scripts/run_server.py
```

Open [http://localhost:8000](http://localhost:8000) in your browser. You should
see the control panel with an empty preview.  The default controller uses the
in-memory mock device so you can experiment immediately. Replace the mock device
with `penplotter.device.GRBL` inside `penplotter/server/app.py` when you are
ready to connect to real hardware.

### 3. Push a pattern from a script or notebook

Create a pattern using the high-level geometry API and send it to the server via
HTTP.  The example below matches the `examples/spiral.py` script:

```python
from penplotter.geometry import Pattern, Polyline
import math, requests

pts = []
for i in range(800):
    t = i / 799
    angle = 10 * 2 * math.pi * t
    r = 100 * t
    pts.append((r * math.cos(angle) + 100, r * math.sin(angle) + 100))

pattern = Pattern().add(Polyline(pts=pts, pen_id=0))
requests.post("http://localhost:8000/api/pattern", json=pattern.to_dict()).raise_for_status()
```

The browser preview updates automatically after pressing **Refresh Preview**. Use
**Start Job** in the UI to execute the queued pattern, or jog/align manually with
the on-screen controls.

### 4. Optional: start plotting

When switching to a GRBL device, instantiate `PlotterController` with a real
`GRBL` instance:

```python
from penplotter.device import Config, GRBL
from penplotter.controller import PlotterController

cfg = Config(port="/dev/ttyUSB0")
controller = PlotterController(device=GRBL(cfg))
controller.connect()
```

## Project layout

```
.
├── penplotter/
│   ├── __init__.py             # Public package API
│   ├── geometry.py             # Geometry primitives and pattern utilities
│   ├── rendering.py            # Plot execution logic
│   ├── controller.py           # High-level orchestration class
│   ├── device/
│   │   ├── __init__.py
│   │   ├── grbl.py             # Production GRBL driver
│   │   └── mock.py             # Development mock device
│   └── server/
│       ├── app.py              # FastAPI application
│       └── static/             # Browser UI (HTML/CSS/JS)
├── scripts/
│   └── run_server.py           # Helper to start the server
├── examples/
│   └── spiral.py               # Minimal script pushing a pattern over HTTP
├── legacy/                     # Original notebook + widget workflow
└── README.md
```

Legacy files are untouched so you can refer back or compare behaviour while the
new modular pipeline becomes the primary entry point.

## API endpoints

Once the server is running the following endpoints are available:

- `GET /api/status` – Combined job, device, and pattern summary.
- `GET /api/pattern` – Current pattern strokes for preview.
- `POST /api/pattern` – Replace the pattern with uploaded JSON.
- `DELETE /api/pattern` – Clear the active pattern.
- `POST /api/job/start` – Start executing the queued pattern.
- `POST /api/job/stop` – Request cancellation.
- `POST /api/device/goto` – Absolute move to the supplied `x` and `y`.
- `POST /api/device/jog` – Relative jog with `dx`/`dy`.
- `POST /api/device/pen` – Set pen height `pos` in `[0, 1]`.
- `POST /api/device/origin` – Set the current position as origin.

These endpoints are intentionally small and JSON-based so custom tooling or
notebooks can integrate easily.

## Development tips

- The mock device stores every commanded position in-memory.  Inspect
  `controller.device.path` while debugging new render logic.
- `Pattern` supports resampling, nearest-neighbour ordering, and chain merging
  directly via `Pattern.resample_polylines` and `Pattern.optimize_order_nn`.
- The browser UI polls `/api/status` every two seconds; adapt the interval in
  `penplotter/server/static/main.js` if needed.

Enjoy plotting!
