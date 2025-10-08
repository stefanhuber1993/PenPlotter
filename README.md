# Pen Plotter Controller

This project provides a NiceGUI + FastAPI application for controlling a GRBL-based pen plotter. It replaces the original Jupyter widget with a standalone web interface for loading SVG artwork, assigning pens, and managing plotting jobs.

## Features

- Upload SVG files with automatic grouping by stroke color.
- Visual preview of the workspace with scaling, rotation, and placement controls.
- Per-pen settings including enable/disable, naming, and individual feed rates.
- Workspace size configuration and automatic centering utilities.
- Connection management for GRBL devices with manual G-code terminal.
- Pen height calibration with configurable servo PWM values.
- Background job manager with start, pause, resume, and stop controls.
- REST endpoint (`/api/status`) exposing connection and job state.

## Getting started

1. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

2. Run the application:

   ```bash
   python main.py
   ```

3. Open your browser to [http://localhost:8080](http://localhost:8080) to access the UI.

## Project structure

- `penplotter/` – Python package containing the application logic.
  - `app.py` – NiceGUI user interface and FastAPI endpoints.
  - `config.py` – Dataclasses for application configuration and state.
  - `grbl_controller.py` – Lightweight GRBL serial wrapper.
  - `plot_manager.py` – Background job execution controller.
  - `svg_loader.py` – SVG parsing utilities based on `svgpathtools`.
  - `toolpath.py` – Geometric transforms and toolpath generation helpers.
- `main.py` – Simple entry point for running the UI server.
- `requirements.txt` – Python dependencies.

## Notes

- The application assumes a GRBL-compatible device with servo-controlled pen up/down commands via `M3`.
- SVG parsing samples curves into polylines using a configurable tolerance. Adjust the "Sampling tolerance" slider if the preview looks too coarse.
- Multi-color SVGs are mapped to separate pens based on the stroke color value. Disable pens you do not wish to plot.
