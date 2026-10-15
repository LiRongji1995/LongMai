"""Local read-only dashboard over selected saved daily replay archives."""
from __future__ import annotations

import asyncio
from pathlib import Path
from datetime import datetime
from typing import Callable

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from ..common import LiveError
from .. import __version__
from .catalog import RunCatalog
from .local_access import LocalAccessMiddleware, WebSocketCapacity


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


INPUT_ERRORS = {"FUTURE_ACCESS_DENIED", "INVALID_AS_OF", "INVALID_INDEX", "INVALID_DATE",
                "DATE_OUT_OF_RANGE", "DATE_NOT_AVAILABLE", "AS_OF_DATE_MISMATCH"}


def create_app(run_dir: str | Path, *, allow_in_progress: bool = True, poll_interval: float = .5,
               now: Callable[[], datetime] | None = None, max_websocket_connections: int = 16) -> FastAPI:
    if not .05 <= poll_interval <= 60:
        raise ValueError("poll_interval must be between .05 and 60 seconds")
    capacity = WebSocketCapacity(max_websocket_connections)
    catalog = RunCatalog(run_dir, allow_in_progress=allow_in_progress, now=now)
    app = FastAPI(title="StockFish Live", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.state.run_view = catalog.single_view
    app.state.run_catalog = catalog
    app.add_middleware(LocalAccessMiddleware, capacity=capacity)

    def invoke(method, date, as_of, *args, **kwargs):
        selection = catalog.select(date, as_of)
        value = getattr(selection.view, method)(*args, as_of=selection.as_of, **kwargs)
        if selection.selected_date is not None:
            value["selected_date"] = selection.selected_date.isoformat()
        return value

    @app.exception_handler(LiveError)
    async def error_handler(_request: Request, exc: LiveError):
        status = 422 if exc.code in INPUT_ERRORS else 503
        return JSONResponse(status_code=status, content={"error": exc.code, "market_edge": "NOT_ESTABLISHED"})

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(FRONTEND / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})

    @app.get("/assets/{asset}", include_in_schema=False)
    def asset_file(asset: str):
        files = {"dashboard.js": "text/javascript", "dashboard.css": "text/css"}
        if asset not in files:
            return JSONResponse(status_code=404, content={"error": "ASSET_NOT_FOUND"})
        return FileResponse(FRONTEND / asset, media_type=files[asset], headers={"Cache-Control": "no-cache"})

    @app.get("/api/live/health")
    def health(as_of: str | None = None, date: str | None = None):
        status = invoke("status", date, as_of)
        return {"service_status": "AVAILABLE", **{key: value for key, value in status.items()
                if key not in ("timeline", "summary", "indices")}}

    @app.get("/api/live/status")
    def status(as_of: str | None = None, date: str | None = None):
        return invoke("status", date, as_of)

    @app.get("/api/live/dates")
    def dates():
        return catalog.dates()

    @app.get("/api/live/comparison")
    def comparison(date: str = Query(...), as_of: str | None = None, index: str = "000001.SH"):
        selection = catalog.select(date, as_of)
        return {**selection.view.comparison(selection.as_of, now=selection.now, identity=index),
                "selected_date": selection.selected_date.isoformat()}

    @app.get("/api/live/market")
    def market(as_of: str | None = None, date: str | None = None):
        return invoke("market", date, as_of)

    @app.get("/api/live/indices")
    def indices(as_of: str | None = None, date: str | None = None):
        result = invoke("market", date, as_of)
        result.pop("market_state")
        result.pop("forecasts")
        result.pop("forecast_revisions")
        return result

    @app.get("/api/live/breadth")
    def breadth(as_of: str | None = None, date: str | None = None):
        return invoke("component", date, as_of, "breadth")

    @app.get("/api/live/sentiment")
    def sentiment(as_of: str | None = None, date: str | None = None):
        return invoke("component", date, as_of, "sentiment")

    @app.get("/api/live/sectors")
    def sectors(as_of: str | None = None, date: str | None = None):
        return invoke("component", date, as_of, "sectors")

    @app.get("/api/live/forecast")
    def forecast(index: str = "000001.SH", as_of: str | None = None, date: str | None = None):
        return invoke("forecast", date, as_of, identity=index)

    @app.get("/api/live/forecast/history")
    def history(index: str = "000001.SH", as_of: str | None = None, date: str | None = None):
        return invoke("history", date, as_of, identity=index)

    @app.get("/api/live/similar-states")
    def similar_states(index: str = "000001.SH", as_of: str | None = None,
                       limit: int = Query(default=10, ge=1, le=10), date: str | None = None):
        return invoke("similar_states", date, as_of, identity=index, limit=limit)

    @app.get("/api/live/warnings")
    def warnings(as_of: str | None = None, date: str | None = None):
        result = invoke("market", date, as_of)
        return {key: value for key, value in result.items()
                if key not in ("market_state", "indices", "actual_paths", "forecasts", "forecast_revisions", "data_quality")}

    @app.websocket("/ws/live")
    async def live_socket(websocket: WebSocket):
        await websocket.accept()
        as_of = websocket.query_params.get("as_of")
        date = websocket.query_params.get("date")
        previous = None
        try:
            while True:
                result = await asyncio.to_thread(invoke, "market", date, as_of)
                identity = (result["snapshot_id"], result["run_status"])
                if identity != previous:
                    await websocket.send_json({"type": "snapshot", **result})
                    previous = identity
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=poll_interval)
                    if message["type"] == "websocket.disconnect":
                        break
                    # Browser pings keep disconnect detection responsive. A
                    # new time selection opens a fresh, time-bounded socket.
                except TimeoutError:
                    pass
        except LiveError as exc:
            await websocket.send_json({"type": "error", "error": exc.code, "market_edge": "NOT_ESTABLISHED"})
            await websocket.close(code=1008 if exc.code in INPUT_ERRORS else 1011)
        except WebSocketDisconnect:
            pass
        except Exception:
            # Never send internal paths or exception text over the socket.
            await websocket.send_json({"type": "error", "error": "LIVE_VIEW_UNAVAILABLE",
                                       "market_edge": "NOT_ESTABLISHED"})
            await websocket.close(code=1011)

    return app
