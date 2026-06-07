"""FastAPI application entry point for the CCBT dashboard v2.

Architecture:
- REST /api/...            JSON snapshots (Pydantic models)
- WS   /ws                 low-freq snapshots (N2)
- WS   /ws/logs            high-freq log tail (N2/N7)
- POST /api/bots/{s}/mode  bot control (N8)
- Static web/dist          Vite SPA served at / (N3+)

Python 3.10+ required — uses pydantic>=2 and modern union syntax.
Run: uvicorn api.main:app --reload --port 8502
"""
from __future__ import annotations

import asyncio
import datetime
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from api.routers import control, metrics, portfolio

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# App lifespan — starts the change-detector background task
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # type: ignore[type-arg]
    """Start the ChangeDetector background task on startup; clean up on shutdown."""
    from api.ws import create_detector, snapshot_registry, log_registry

    data_dir = os.getenv("BOT_DATA_DIR", str(REPO / "data"))
    db_path_env = os.getenv("BOT_DATA_DIR", "")
    if db_path_env and Path(db_path_env, "trades.db").exists():
        db_path = str(Path(db_path_env) / "trades.db")
    else:
        db_path = str(REPO / "trades.db")

    log_path = str(REPO / "trading_bot.log")

    detector = create_detector(db_path=db_path, data_dir=data_dir, log_path=log_path)
    task = asyncio.create_task(detector.run_forever())

    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        detector.close()


app = FastAPI(
    title="CCBT Dashboard API",
    description="FastAPI backend for the CCBT crypto trading bot dashboard (v2).",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow Vite dev server (port 5173) in development
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev
        "http://localhost:8502",   # self (prod)
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8502",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(portfolio.router)
app.include_router(metrics.router)
app.include_router(control.router)

# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
from api.models import HealthResponse  # noqa: E402


@app.get("/api/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    """Liveness probe — confirms the API process is up."""
    db_path = REPO / "trades.db"
    data_dir = os.getenv("BOT_DATA_DIR", "")
    if data_dir and Path(data_dir, "trades.db").exists():
        db_path = Path(data_dir) / "trades.db"
    return HealthResponse(status="ok", db_exists=db_path.exists(), version="2")


# ---------------------------------------------------------------------------
# WebSocket: /ws — low-frequency portfolio + bots snapshot channel
# ---------------------------------------------------------------------------

from api.ws import snapshot_registry  # noqa: E402


@app.websocket("/ws")
async def ws_snapshots(websocket: WebSocket) -> None:
    """Low-frequency snapshot channel (portfolio + bots, every ~3s). N2.

    Protocol:
    - On connect: client is registered; ChangeDetector broadcasts an initial
      snapshot within the next tick (≤ poll_interval seconds).
    - On change: exactly one snapshot broadcast per tick (coalesced).
    - Envelope: {type: 'snapshot'|'heartbeat', ts: ISO8601, data: {...}}
    - Client auto-reconnects with backoff (N3 hook).
    """
    await websocket.accept()
    snapshot_registry.add(websocket)

    # Send an immediate initial snapshot so the UI doesn't wait for the first tick
    from api.ws import _detector
    if _detector is not None:
        try:
            snap = _detector._build_snapshot()
            await websocket.send_json(snap)
        except Exception:
            pass
    else:
        # Fallback before detector is initialised (e.g. TestClient without lifespan)
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await websocket.send_json({"type": "snapshot", "ts": ts, "data": {}})

    try:
        # Keep the connection open; receive loop (handles pings and close frames)
        while True:
            data = await websocket.receive_text()
            # Echo heartbeats; ignore other client messages
            if data == "ping":
                await websocket.send_json(
                    {
                        "type": "heartbeat",
                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "data": {},
                    }
                )
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        snapshot_registry.remove(websocket)


# ---------------------------------------------------------------------------
# WebSocket: /ws/logs — high-frequency log-tail channel (N7)
# ---------------------------------------------------------------------------

from api.ws import log_registry as _log_registry  # noqa: E402


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket) -> None:
    """High-frequency log tail channel (separate from /ws — backpressure isolated). N7.

    Slow log consumers cannot stall /ws snapshot delivery because each channel
    uses its own ConnectionRegistry.  N7 wires up the actual log-tail producer.
    """
    await websocket.accept()
    _log_registry.add(websocket)

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await websocket.send_json(
        {
            "type": "log",
            "ts": ts,
            "data": {"lines": [], "message": "Log streaming ready (N7 fills lines)"},
        }
    )

    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        _log_registry.remove(websocket)


# ---------------------------------------------------------------------------
# Serve Vite SPA static files (N3 — only if web/dist exists)
# ---------------------------------------------------------------------------
_dist = REPO / "web" / "dist"
if _dist.exists():
    from fastapi.staticfiles import StaticFiles  # noqa: E402

    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="spa")
