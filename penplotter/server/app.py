"""FastAPI application that powers the browser based control panel."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..controller import PlotterController
from ..geometry import Pattern

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"


def create_controller() -> PlotterController:
    controller = PlotterController()
    controller.connect()
    return controller


controller = create_controller()
app = FastAPI(title="PenPlotter Control Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def status() -> Dict[str, Any]:
    return {
        "job": controller.job_status(),
        "device": controller.device_status(),
        "pattern": controller.pattern_summary(),
    }


@app.get("/api/pattern")
def get_pattern() -> Dict[str, Any]:
    return controller.pattern_strokes()


@app.post("/api/pattern")
def post_pattern(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        pattern = controller.load_pattern_dict(payload)
    except Exception as exc:  # pragma: no cover - runtime validation
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    bbox = pattern.bounding_box()
    return {
        "ok": True,
        "count": len(pattern.items),
        "bounding_box": bbox,
    }


@app.delete("/api/pattern")
def clear_pattern() -> Dict[str, Any]:
    controller.set_pattern(Pattern())
    return {"ok": True}


@app.post("/api/job/start")
def start_job(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    try:
        controller.start_job(options=payload or {})
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


@app.post("/api/job/stop")
def stop_job() -> Dict[str, Any]:
    controller.stop_job()
    return {"ok": True}


@app.post("/api/device/goto")
def device_goto(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        x = float(payload["x"])
        y = float(payload["y"])
    except Exception as exc:  # pragma: no cover - runtime validation
        raise HTTPException(status_code=400, detail="x and y are required") from exc
    controller.goto(x, y)
    return {"ok": True}


@app.post("/api/device/jog")
def device_jog(payload: Dict[str, Any]) -> Dict[str, Any]:
    dx = float(payload.get("dx", 0.0))
    dy = float(payload.get("dy", 0.0))
    controller.jog(dx, dy)
    return {"ok": True}


@app.post("/api/device/pen")
def device_pen(payload: Dict[str, Any]) -> Dict[str, Any]:
    controller.pen_height(float(payload.get("pos", 1.0)))
    return {"ok": True}


@app.post("/api/device/origin")
def device_origin() -> Dict[str, Any]:
    controller.set_origin_here()
    return {"ok": True}


__all__ = ["app", "controller", "create_controller"]
