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

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from api.deps import assert_startup_safety
from api.routers import candles, control, metrics, portfolio

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# App lifespan — starts the change-detector background task
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:  # type: ignore[type-arg]
    """Start the ChangeDetector background task on startup; clean up on shutdown."""
    from api.ws import create_detector, snapshot_registry, log_registry

    # Safety gate: refuse to start on a non-loopback interface without a token.
    # Behind nginx the localhost peer-check is vacuous; the token provides real protection.
    host = os.getenv("CCBT_DASH_HOST", "127.0.0.1")
    from api.deps import CCBT_DASH_TOKEN
    assert_startup_safety(host=host, token=CCBT_DASH_TOKEN)

    data_dir = os.getenv("BOT_DATA_DIR", str(REPO / "data"))
    db_path_env = os.getenv("BOT_DATA_DIR", "")
    if db_path_env and Path(db_path_env, "trades.db").exists():
        db_path = str(Path(db_path_env) / "trades.db")
    else:
        db_path = str(REPO / "trades.db")

    log_path = str(REPO / "trading_bot.log")

    detector = create_detector(db_path=db_path, data_dir=data_dir, log_path=log_path)
    task = asyncio.create_task(detector.run_forever())

    # Start the realtime markPrice WS client (public Binance stream — no API key).
    from api.markprice import create_client as create_markprice_client
    mp_client = create_markprice_client(
        db_path=db_path,
        broadcast_fn=snapshot_registry.broadcast,
    )
    mp_task = asyncio.create_task(mp_client.run_forever())

    try:
        yield
    finally:
        # Cancel markprice task first (it may be sleeping between reconnects)
        mp_task.cancel()
        try:
            await mp_task
        except asyncio.CancelledError:
            pass
        mp_client.close()

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
app.include_router(candles.router)

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


def _check_ws_token(token: Optional[str]) -> bool:
    """Return True if the WS connection is authorised.

    When CCBT_DASH_TOKEN is unset, all connections are allowed (local dev).
    When set, the supplied token must match exactly.
    """
    from api.deps import CCBT_DASH_TOKEN
    if not CCBT_DASH_TOKEN:
        return True  # No token configured — open access (local dev)
    return token == CCBT_DASH_TOKEN


@app.websocket("/ws")
async def ws_snapshots(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
) -> None:
    """Low-frequency snapshot channel (portfolio + bots, every ~3s). N2.

    Protocol:
    - On connect: client is registered; ChangeDetector broadcasts an initial
      snapshot within the next tick (≤ poll_interval seconds).
    - On change: exactly one snapshot broadcast per tick (coalesced).
    - Envelope: {type: 'snapshot'|'heartbeat', ts: ISO8601, data: {...}}
    - Client auto-reconnects with backoff (N3 hook).
    - Auth: pass ?token=<CCBT_DASH_TOKEN> query param when token is set.
    """
    await websocket.accept()

    if not _check_ws_token(token):
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await websocket.send_json({"type": "error", "ts": ts, "data": {"detail": "Unauthorised"}})
        await websocket.close(code=4403)
        return

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
async def ws_logs(
    websocket: WebSocket,
    token: Optional[str] = Query(default=None),
) -> None:
    """High-frequency log tail channel (separate from /ws — backpressure isolated). N7.

    Slow log consumers cannot stall /ws snapshot delivery because each channel
    uses its own ConnectionRegistry.  N7 wires up the actual log-tail producer.
    Auth: pass ?token=<CCBT_DASH_TOKEN> query param when token is set.
    """
    await websocket.accept()

    if not _check_ws_token(token):
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        await websocket.send_json({"type": "error", "ts": ts, "data": {"detail": "Unauthorised"}})
        await websocket.close(code=4403)
        return

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
# SPA deep-link catch-all (N3) — must be registered AFTER all /api and /ws routes.
#
# When the Vite SPA navigates to /dashboard/bot/BTCUSDT directly, the browser
# requests that path from the server.  FastAPI's static-file mount (html=True)
# would serve index.html for paths that match files, but deep-links to
# client-side routes don't have corresponding files in web/dist — causing 404s.
#
# A plain GET catch-all route registered before the StaticFiles mount intercepts
# all non-/api non-/ws paths and always returns index.html, letting the SPA
# router take over.  Only active when web/dist exists.
#
# Token injection: when CCBT_DASH_TOKEN is set, a <script> block is prepended
# to the <head> tag that writes window.__CCBT_TOKEN__ = "<token>" so the
# browser's buildWsUrl() can include ?token= in WS URLs.  Without this the
# live WS channel is silently dead behind any token-gated deployment (nginx
# basic-auth outer gate + FastAPI token inner gate).
# ---------------------------------------------------------------------------
_dist = REPO / "web" / "dist"
if _dist.exists():
    import mimetypes as _mimetypes  # noqa: E402
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse  # noqa: E402

    def _inject_token(html: str, token: Optional[str]) -> str:
        """Prepend a window.__CCBT_TOKEN__ assignment to the HTML <head>.

        The token value is JSON-encoded so it is safe to embed in a JS string
        literal (handles quotes, backslashes, etc.).  The </script> sequence is
        escaped to prevent injection via a token value that itself contains it.
        """
        import json as _json
        if not token:
            return html
        # Escape </script> so a token containing it cannot break out of the tag.
        safe_token = _json.dumps(token).replace("</", "<\\/")
        snippet = f"<script>window.__CCBT_TOKEN__={safe_token};</script>"
        # Insert just before </head> so it runs before any module scripts
        if "</head>" in html:
            return html.replace("</head>", f"{snippet}</head>", 1)
        # Fallback: prepend to body (no </head> found — minimal HTML)
        return snippet + html

    from fastapi.responses import Response as _Response  # noqa: E402

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_catch_all(full_path: str) -> _Response:
        """Serve static assets or index.html for SPA deep-links (N3).

        Priority:
        1. /api/* and /ws/* paths → 404 JSON (no token in body, to avoid leaking it).
           These should have been handled by earlier-registered routes; this branch
           is a safety net for /api/nonexistent and /ws/bogus.
           Guard uses startswith("api/") or == "api" (and "ws/" / "ws") so that
           SPA routes like /apikeys or /wstools still receive index.html.
        2. full_path resolves to a real file under _dist (e.g. assets/*.js) →
           FileResponse with correct MIME type.  Path traversal is blocked by
           checking the resolved path is still under _dist.
        3. Everything else → inject token into index.html for client-side routing.
           index.html is served with Cache-Control: no-store so browsers never
           cache a stale token injection.
        """
        # --- 1. Guard: no-token 404 for /api/* and /ws/* only
        #   startswith("api") would wrongly catch /apikeys, /apistuff, etc.
        #   We gate only on the exact segment boundary.
        if (
            full_path == "api"
            or full_path.startswith("api/")
            or full_path == "ws"
            or full_path.startswith("ws/")
        ):
            return JSONResponse(
                status_code=404,
                content={"detail": f"Not Found: /{full_path}"},
            )

        # --- 2. Serve real static files (assets/, favicons, etc.)
        candidate = (_dist / full_path).resolve()
        try:
            # Block path traversal: resolved path must still sit under _dist
            candidate.relative_to(_dist.resolve())
            is_under_dist = True
        except ValueError:
            is_under_dist = False

        if is_under_dist and candidate.is_file():
            mime, _ = _mimetypes.guess_type(str(candidate))
            return FileResponse(str(candidate), media_type=mime or "application/octet-stream")

        # --- 3. SPA catch-all: serve injected index.html
        index = _dist / "index.html"
        if index.exists():
            from api.deps import CCBT_DASH_TOKEN  # noqa: PLC0415
            html = index.read_text(encoding="utf-8")
            html = _inject_token(html, CCBT_DASH_TOKEN)
            return HTMLResponse(
                content=html,
                status_code=200,
                headers={"Cache-Control": "no-store"},
            )

        # No SPA bundle at all
        return JSONResponse(status_code=404, content={"detail": "SPA bundle not found"})
