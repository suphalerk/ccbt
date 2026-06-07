"""N2 — WebSocket live layer for the CCBT dashboard API.

Architecture:
- A FastAPI lifespan background task runs ``ChangeDetector`` every
  ``CCBT_DASH_POLL_S`` seconds (default 3 s).
- ``ChangeDetector`` watches four sources:
    1. DB ``PRAGMA data_version`` (on a process-lifetime RO/autocommit connection)
    2. ``data/heartbeat_*`` file mtimes (bot online/offline)
    3. ``data/mode_*.json`` file mtimes (mode changes)
    4. ``trading_bot.log`` size + inode (appends / log rotation)
- On any change → recompute the portfolio snapshot once (coalesced) and
  broadcast to all registered ``/ws`` clients.
- ``/ws/logs`` uses a separate channel so a slow log consumer can't stall
  snapshot delivery.
- ``ConnectionRegistry`` removes dead sockets on send errors.

WAL safety (must-fix #7):
- RO connection opened with ``mode=ro`` URI + ``PRAGMA query_only=1``.
- ``isolation_level=None`` (autocommit) — no transaction ever opened.
- ``conn.in_transaction`` must be ``False`` after every PRAGMA/SELECT.
- Graceful retry if DB not yet in WAL at startup.
- No WS clients → skip recompute/broadcast (cheap compare still runs).

Python ≥ 3.10 (venv).  ``from __future__ import annotations`` for forward refs.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
_DEFAULT_POLL_S: float = float(os.getenv("CCBT_DASH_POLL_S", "3"))
_HB_GLOB = "heartbeat_*"
_MODE_GLOB = "mode_*.json"
_LOG_FILENAME = "trading_bot.log"


# ---------------------------------------------------------------------------
# ConnectionRegistry — thread-safe add/remove; broadcast drops dead sockets
# ---------------------------------------------------------------------------


class ConnectionRegistry:
    """Holds a set of live WebSocket connections with safe add/remove/broadcast."""

    def __init__(self) -> None:
        self._clients: List[Any] = []  # list of WebSocket objects
        self._lock = asyncio.Lock()

    def add(self, ws: Any) -> None:
        """Register a new WebSocket connection (called from the WS handler)."""
        # Non-async add via list append; safe because asyncio is single-threaded
        self._clients.append(ws)

    def remove(self, ws: Any) -> None:
        """Deregister a WebSocket connection."""
        try:
            self._clients.remove(ws)
        except ValueError:
            pass

    def count(self) -> int:
        """Return current number of registered clients."""
        return len(self._clients)

    async def broadcast(self, msg: Dict[str, Any]) -> None:
        """Send ``msg`` to all registered clients; silently drop dead ones."""
        dead: List[Any] = []
        for ws in list(self._clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


# Two separate registries — keeps log stream backpressure isolated from snapshots
snapshot_registry = ConnectionRegistry()
log_registry = ConnectionRegistry()


# ---------------------------------------------------------------------------
# Watched-state snapshot helpers
# ---------------------------------------------------------------------------


def _file_state(path: Path) -> Optional[Tuple[float, int]]:
    """Return ``(mtime, inode)`` or ``None`` if the file doesn't exist."""
    try:
        st = path.stat()
        return (st.st_mtime, st.st_ino)
    except OSError:
        return None


def _glob_mtimes(data_dir: Path, pattern: str) -> Dict[str, float]:
    """Return ``{filename: mtime}`` for all files matching *pattern* in *data_dir*."""
    result: Dict[str, float] = {}
    try:
        for p in data_dir.glob(pattern):
            try:
                result[p.name] = p.stat().st_mtime
            except OSError:
                pass
    except OSError:
        pass
    return result


# ---------------------------------------------------------------------------
# ChangeDetector
# ---------------------------------------------------------------------------


class ChangeDetector:
    """Polls four watched sources; on change calls ``_snapshot_broadcast``.

    Designed to be instantiated once at app startup and run as a background
    asyncio task.  The persistent read-only SQLite connection is opened lazily
    (first successful ``_try_get_data_version`` call).
    """

    def __init__(
        self,
        db_path: str,
        data_dir: str,
        poll_interval: float = _DEFAULT_POLL_S,
        log_path: Optional[str] = None,
    ) -> None:
        self.db_path = db_path
        self.data_dir = Path(data_dir)
        self.poll_interval = poll_interval
        self.log_path = Path(log_path) if log_path else REPO / _LOG_FILENAME

        # Persistent RO connection — opened lazily
        self._ro_conn: Optional[sqlite3.Connection] = None

        # Baseline state (None = not yet initialised)
        self._last_state: Optional[Dict[str, Any]] = None

        # The broadcast callable (overrideable for tests)
        self._snapshot_broadcast: Callable[[Dict[str, Any]], Any] = snapshot_registry.broadcast

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _open_ro_connection(self) -> Optional[sqlite3.Connection]:
        """Open a read-only autocommit SQLite connection; returns None on error."""
        db = Path(self.db_path)
        if not db.exists():
            return None
        try:
            conn = sqlite3.connect(
                f"file:{db}?mode=ro",
                uri=True,
                isolation_level=None,  # autocommit — must NEVER open a transaction
                check_same_thread=False,
                timeout=2.0,
            )
            conn.execute("PRAGMA query_only=1")
            return conn
        except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
            logger.debug("ChangeDetector: could not open RO connection: %s", exc)
            return None

    def _ensure_ro_conn(self) -> Optional[sqlite3.Connection]:
        """Return the persistent RO connection; open it if not yet available."""
        if self._ro_conn is None:
            self._ro_conn = self._open_ro_connection()
        return self._ro_conn

    def _try_get_data_version(self) -> Optional[int]:
        """Read ``PRAGMA data_version``; return ``None`` on any error.

        Must NEVER leave the connection in a transaction (``isolation_level=None``
        guarantees this, but we assert for safety).
        """
        conn = self._ensure_ro_conn()
        if conn is None:
            return None
        try:
            row = conn.execute("PRAGMA data_version").fetchone()
            # Sanity-check: autocommit means we're never in a transaction
            if conn.in_transaction:
                logger.warning(
                    "ChangeDetector: RO connection unexpectedly in a transaction — closing"
                )
                try:
                    conn.close()
                except Exception:
                    pass
                self._ro_conn = None
                return None
            return int(row[0]) if row else None
        except (sqlite3.OperationalError, sqlite3.DatabaseError, Exception) as exc:
            logger.debug("ChangeDetector: data_version read failed: %s", exc)
            # Reset the connection so it's re-opened on the next tick
            try:
                conn.close()
            except Exception:
                pass
            self._ro_conn = None
            return None

    # ------------------------------------------------------------------
    # Watched-state snapshot
    # ------------------------------------------------------------------

    def _snapshot_state(self) -> Dict[str, Any]:
        """Capture current state of all four watched sources."""
        return {
            "data_version": self._try_get_data_version(),
            "heartbeats": _glob_mtimes(self.data_dir, _HB_GLOB),
            "modes": _glob_mtimes(self.data_dir, _MODE_GLOB),
            "log": _file_state(self.log_path),
        }

    def _state_changed(
        self, old: Dict[str, Any], new: Dict[str, Any]
    ) -> bool:
        """Return True if any watched source changed between *old* and *new*."""
        if old["data_version"] != new["data_version"]:
            return True
        if old["heartbeats"] != new["heartbeats"]:
            return True
        if old["modes"] != new["modes"]:
            return True
        if old["log"] != new["log"]:
            return True
        return False

    # ------------------------------------------------------------------
    # Snapshot computation
    # ------------------------------------------------------------------

    def _build_snapshot(self) -> Dict[str, Any]:
        """Build a portfolio+bots snapshot dict from queries.py (best-effort)."""
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        payload: Dict[str, Any] = {
            "type": "snapshot",
            "ts": ts,
            "data": {},
        }
        db = Path(self.db_path)
        if not db.exists():
            return payload
        try:
            import sys
            if str(REPO) not in sys.path:
                sys.path.insert(0, str(REPO))
            from dashboard.queries import get_trade_stats, get_per_bot_summary  # type: ignore[import]

            stats = get_trade_stats(db_path=self.db_path)
            per_bot_df = get_per_bot_summary(db_path=self.db_path)

            import math

            def _safe(v: Any, default: Any = None) -> Any:
                if v is None:
                    return default
                if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                    return default
                return v

            portfolio: Dict[str, Any] = {
                "total_trades": int(stats.get("total_trades") or 0),
                "win_rate_pct": round(float(_safe(stats.get("win_rate"), 0.0)) * 100, 1),
                "profit_factor": _safe(stats.get("profit_factor"), 0.0),
                "total_pnl": round(float(_safe(stats.get("total_pnl"), 0.0)), 2),
            }

            bots: List[Dict[str, Any]] = []
            if not per_bot_df.empty and "symbol" in per_bot_df.columns:
                for _, row in per_bot_df.head(50).iterrows():
                    bots.append(
                        {
                            "symbol": str(row.get("symbol", "")),
                            "total_pnl": round(float(_safe(row.get("total_pnl"), 0.0)), 2),
                            "win_rate_pct": round(
                                float(_safe(row.get("win_rate"), 0.0)) * 100, 1
                            ),
                            "trade_count": int(_safe(row.get("trade_count"), 0)),
                        }
                    )

            payload["data"] = {"portfolio": portfolio, "bots": bots}
        except Exception as exc:
            logger.debug("ChangeDetector: snapshot build error: %s", exc)
            # Return envelope with empty data — never crash the broadcaster
        return payload

    # ------------------------------------------------------------------
    # Main tick
    # ------------------------------------------------------------------

    async def _tick(self) -> None:
        """Run one detection tick.  Coalesces all source checks into one broadcast."""
        current_state = self._snapshot_state()

        if self._last_state is None:
            # First tick — initialise baseline and broadcast immediately (initial snapshot)
            self._last_state = current_state
            snapshot = self._build_snapshot()
            await self._snapshot_broadcast(snapshot)
            return

        changed = self._state_changed(self._last_state, current_state)
        if changed:
            # Update baseline first (before broadcast — so a crash in broadcast
            # doesn't cause an infinite re-broadcast loop)
            self._last_state = current_state
            if snapshot_registry.count() > 0:
                snapshot = self._build_snapshot()
                await self._snapshot_broadcast(snapshot)

    async def run_forever(self) -> None:
        """Background loop — runs until cancelled."""
        logger.info(
            "ChangeDetector: starting (poll_interval=%.1fs, db=%s)",
            self.poll_interval,
            self.db_path,
        )
        while True:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("ChangeDetector: tick error: %s", exc)
            await asyncio.sleep(self.poll_interval)

    def close(self) -> None:
        """Release the persistent RO connection."""
        if self._ro_conn is not None:
            try:
                self._ro_conn.close()
            except Exception:
                pass
            self._ro_conn = None


# ---------------------------------------------------------------------------
# Module-level singleton (created by the lifespan in api/main.py)
# ---------------------------------------------------------------------------

_detector: Optional[ChangeDetector] = None


def get_detector() -> Optional[ChangeDetector]:
    return _detector


def create_detector(db_path: str, data_dir: str, log_path: Optional[str] = None) -> ChangeDetector:
    global _detector
    _detector = ChangeDetector(
        db_path=db_path,
        data_dir=data_dir,
        log_path=log_path,
    )
    return _detector
