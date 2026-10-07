"""Local read-only dashboard over a single saved or in-progress replay run."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from ..common import LiveError
from .. import __version__
from .view import RunView


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def create_app(run_dir: str | Path, *, allow_in_progress: bool = True, poll_interval: float = .5) -> FastAPI:
    if not .05 <= poll_interval <= 60:
        raise ValueError("poll_interval must be between .05 and 60 seconds")
    view = RunView(run_dir, allow_in_progress=allow_in_progress)
    app = FastAPI(title="StockFish Live", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.state.run_view = view

    @app.exception_handler(LiveError)
    async def error_handler(_request: Request, exc: LiveError):
        status = 422 if exc.code in {"FUTURE_ACCESS_DENIED", "INVALID_AS_OF", "INVALID_INDEX"} else 503
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
    def health(as_of: str | None = None):
        status = view.status(as_of)
        return {"service_status": "AVAILABLE", **{key: value for key, value in status.items()
                if key not in ("timeline", "summary", "indices")}}

    @app.get("/api/live/status")
    def status(as_of: str | None = None):
        return view.status(as_of)

    @app.get("/api/live/market")
    def market(as_of: str | None = None):
        return view.market(as_of)

    @app.get("/api/live/indices")
    def indices(as_of: str | None = None):
        result = view.market(as_of)
        result.pop("market_state")
        result.pop("forecasts")
        result.pop("forecast_revisions")
        return result

    @app.get("/api/live/breadth")
    def breadth(as_of: str | None = None):
        return view.component("breadth", as_of)

    @app.get("/api/live/sentiment")
    def sentiment(as_of: str | None = None):
        return view.component("sentiment", as_of)

    @app.get("/api/live/sectors")
    def sectors(as_of: str | None = None):
        return view.component("sectors", as_of)

    @app.get("/api/live/forecast")
    def forecast(index: str = "000001.SH", as_of: str | None = None):
        return view.forecast(index, as_of)

    @app.get("/api/live/forecast/history")
    def history(index: str = "000001.SH", as_of: str | None = None):
        return view.history(index, as_of)

    @app.get("/api/live/similar-states")
    def similar_states(index: str = "000001.SH", as_of: str | None = None,
                       limit: int = Query(default=10, ge=1, le=10)):
        return view.similar_states(index, as_of, limit)

    @app.get("/api/live/warnings")
    def warnings(as_of: str | None = None):
        result = view.market(as_of)
        return {key: value for key, value in result.items()
                if key not in ("market_state", "indices", "actual_paths", "forecasts", "forecast_revisions", "data_quality")}

    @app.websocket("/ws/live")
    async def live_socket(websocket: WebSocket):
        await websocket.accept()
        as_of = websocket.query_params.get("as_of")
        previous = None
        try:
            while True:
                result = await asyncio.to_thread(view.market, as_of)
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
            await websocket.close(code=1008 if exc.code == "FUTURE_ACCESS_DENIED" else 1011)
        except WebSocketDisconnect:
            pass

    return app
