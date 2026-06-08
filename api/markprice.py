"""api/markprice.py — Realtime unrealized PnL via Binance PUBLIC futures markPrice WS.

Architecture
------------
- ONE outbound websockets connection to the Binance PUBLIC combined stream endpoint.
  Testnet:  wss://stream.binancefuture.com/stream
  Mainnet:  wss://fstream.binance.com/stream
- NO API key / NO account access / NO REST calls.  Public market-data only.
- Subscribes to ``<symbol>@markPrice`` streams for every symbol that has an open
  position in trades.db (status='open').
- Computes uPnL in Python: (mark - entry) * size * (1 if long else -1).
- Maintains a {symbol -> PositionMark} cache with the latest mark + computed uPnL.
- Broadcasts ``{type:'upnl', ...}`` on the existing snapshot_registry at most once
  per second (throttled), keeping the feed from flooding snapshot clients.
- Reconnects with exponential backoff; marks feed_status='offline' while down and
  retains last-known values so consumers degrade gracefully.
- DB access: read-only query_only=1 connection, same pattern as ChangeDetector.
  Polls open symbols every SYMBOL_POLL_S seconds; re-subscribes when the set changes.

Symbol mapping
--------------
DB stores raw symbols like ``BTCUSDT``.  Binance combined stream uses lower-case:
  subscribe key: ``btcusdt@markPrice``
  Reverse map: ``BTCUSDT`` (upper) ← stream ``btcusdt@markPrice``

Side normalisation
------------------
DB ``side`` column stores ``'long'`` / ``'buy'`` (long) or ``'short'`` / ``'sell'`` (short).
Long  → multiplier = +1
Short → multiplier = -1

Testnet / mainnet selection
---------------------------
Reads ``CCBT_USE_TESTNET`` env (default "1" = testnet) OR the config file
``use_testnet`` key.  The env var is checked at module import; call-time config
file reads are not done to keep this module dependency-free (no ccxt / bot.config).
Set ``CCBT_MARKPRICE_TESTNET=0`` to force mainnet explicitly.
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config constants
# ---------------------------------------------------------------------------

_TESTNET_WS_URL = "wss://stream.binancefuture.com/stream"
_MAINNET_WS_URL = "wss://fstream.binance.com/stream"

SYMBOL_POLL_S: float = float(os.getenv("CCBT_MARKPRICE_POLL_S", "5"))
BROADCAST_MIN_INTERVAL_S: float = 1.0           # throttle: at most 1 broadcast/second
BACKOFF_BASE_S: float = 1.0
BACKOFF_MAX_S: float = 60.0
STALE_AFTER_S: float = float(os.getenv("CCBT_MARKPRICE_STALE_S", "15"))


def _is_testnet() -> bool:
    """Resolve testnet flag: env CCBT_MARKPRICE_TESTNET > config.json > default testnet."""
    explicit = os.getenv("CCBT_MARKPRICE_TESTNET")
    if explicit is not None:
        return explicit.strip() not in ("0", "false", "no")
    # Fall back: check the primary config file
    repo = Path(__file__).resolve().parent.parent
    for name in ("config.json", "config_btcusdt_emacross.json"):
        cfg_path = repo / name
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text())
                return bool(cfg.get("use_testnet", True))
            except Exception:
                pass
    return True  # safe default


def _ws_base_url() -> str:
    return _TESTNET_WS_URL if _is_testnet() else _MAINNET_WS_URL


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class PositionMark:
    """Latest mark + computed uPnL for one open position."""

    __slots__ = ("symbol", "side", "entry_price", "size", "mark_price", "upnl", "ts")

    def __init__(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        size: float,
        mark_price: float = 0.0,
        upnl: float = 0.0,
        ts: float = 0.0,
    ) -> None:
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.size = size
        self.mark_price = mark_price
        self.upnl = upnl
        self.ts = ts  # unix timestamp of last mark update

    def compute_upnl(self, mark: float) -> float:
        """(mark - entry) * size * direction."""
        direction = 1.0 if self.side.lower() in ("long", "buy") else -1.0
        return (mark - self.entry_price) * self.size * direction

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "entry_price": self.entry_price,
            "size": self.size,
            "mark_price": self.mark_price,
            "upnl": round(self.upnl, 6),
            "ts": (
                datetime.datetime.fromtimestamp(self.ts, tz=datetime.timezone.utc).isoformat()
                if self.ts
                else None
            ),
        }


# ---------------------------------------------------------------------------
# DB helpers — read-only, query_only=1, same pattern as ChangeDetector
# ---------------------------------------------------------------------------


def _open_ro_conn(db_path: str) -> Optional[sqlite3.Connection]:
    db = Path(db_path)
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(
            f"file:{db}?mode=ro",
            uri=True,
            isolation_level=None,
            check_same_thread=False,
            timeout=2.0,
        )
        conn.execute("PRAGMA query_only=1")
        return conn
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as exc:
        logger.debug("markprice: could not open RO connection: %s", exc)
        return None


def _query_open_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """Return open positions from trades: [{symbol, side, entry_price, size}, ...]."""
    try:
        rows = conn.execute(
            "SELECT symbol, side, entry_price, size FROM trades WHERE status='open'"
        ).fetchall()
        positions = []
        for symbol, side, entry_price, size in rows:
            if symbol and side and entry_price is not None and size is not None:
                positions.append(
                    {
                        "symbol": str(symbol).upper(),
                        "side": str(side).lower(),
                        "entry_price": float(entry_price),
                        "size": abs(float(size)),
                    }
                )
        return positions
    except Exception as exc:
        logger.debug("markprice: query open positions failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# MarkPriceClient
# ---------------------------------------------------------------------------


class MarkPriceClient:
    """Maintains ONE outbound WS connection to the Binance public markPrice stream.

    Usage (called from lifespan):
        client = MarkPriceClient(db_path=..., broadcast_fn=...)
        task = asyncio.create_task(client.run_forever())
        ...
        task.cancel(); await task

    No API key is used or stored.
    """

    def __init__(
        self,
        db_path: str,
        broadcast_fn: Any,                       # async callable (msg) → None
        ws_base_url: Optional[str] = None,
    ) -> None:
        self._db_path = db_path
        self._broadcast_fn = broadcast_fn
        self._ws_base_url = ws_base_url or _ws_base_url()

        # Internal state
        self._ro_conn: Optional[sqlite3.Connection] = None
        self._positions: Dict[str, PositionMark] = {}      # keyed by upper symbol
        self._open_symbols: Set[str] = set()               # last known set
        self._last_broadcast_ts: float = 0.0
        self._feed_status: str = "offline"
        self._retry_count: int = 0
        self._last_mark_ts: float = 0.0                    # wall clock of last tick
        self._needs_reconnect: bool = False                 # set by _symbol_poll_loop

    # ------------------------------------------------------------------
    # Public read — snapshot used by broadcaster and tests
    # ------------------------------------------------------------------

    def get_upnl_payload(self) -> Dict[str, Any]:
        """Build the upnl broadcast payload dict."""
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        total = sum(p.upnl for p in self._positions.values())
        # Determine effective feed status:
        # if last mark is older than STALE_AFTER_S, degrade to 'stale'
        if self._feed_status == "live" and self._last_mark_ts > 0:
            age = time.monotonic() - self._last_mark_ts
            effective = "stale" if age > STALE_AFTER_S else "live"
        else:
            effective = self._feed_status
        return {
            "type": "upnl",
            "ts": ts,
            "data": {
                "positions": [p.to_dict() for p in self._positions.values()],
                "total_upnl": round(total, 6),
                "feed_status": effective,
            },
        }

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _ensure_ro_conn(self) -> Optional[sqlite3.Connection]:
        if self._ro_conn is None:
            self._ro_conn = _open_ro_conn(self._db_path)
        return self._ro_conn

    def _reload_positions(self) -> Set[str]:
        """Re-read open positions from DB; return new symbol set."""
        conn = self._ensure_ro_conn()
        if conn is None:
            return set()
        rows = _query_open_positions(conn)
        new_positions: Dict[str, PositionMark] = {}
        for row in rows:
            sym = row["symbol"]
            existing = self._positions.get(sym)
            if existing is not None:
                # Preserve mark + uPnL from existing (already live-updated)
                existing.entry_price = row["entry_price"]
                existing.size = row["size"]
                existing.side = row["side"]
                new_positions[sym] = existing
            else:
                new_positions[sym] = PositionMark(
                    symbol=sym,
                    side=row["side"],
                    entry_price=row["entry_price"],
                    size=row["size"],
                )
        self._positions = new_positions
        return set(new_positions.keys())

    # ------------------------------------------------------------------
    # WS helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _stream_name(symbol: str) -> str:
        """BTCUSDT → btcusdt@markPrice"""
        return f"{symbol.lower()}@markPrice"

    @classmethod
    def _build_ws_url(cls, symbols: Set[str], base_url: str) -> str:
        """Combined stream URL for N symbols."""
        streams = "/".join(cls._stream_name(s) for s in sorted(symbols))
        return f"{base_url}?streams={streams}"

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Run until cancelled — reconnects with exponential backoff."""
        logger.info("markprice: starting (db=%s, url=%s)", self._db_path, self._ws_base_url)
        while True:
            try:
                await self._run_once()
            except asyncio.CancelledError:
                logger.info("markprice: cancelled — stopping")
                self._feed_status = "offline"
                await self._maybe_broadcast(force=True)
                raise
            except Exception as exc:
                logger.warning("markprice: connection error: %s", exc)

            # On any disconnect: mark offline, emit a stale payload
            self._feed_status = "offline"
            await self._maybe_broadcast(force=True)

            # Exponential backoff
            delay = min(BACKOFF_BASE_S * (2 ** self._retry_count), BACKOFF_MAX_S)
            self._retry_count += 1
            logger.info("markprice: reconnecting in %.1fs (attempt #%d)", delay, self._retry_count)
            await asyncio.sleep(delay)

    async def _run_once(self) -> None:
        """Single WS session.  Exits (raises or returns) when connection drops."""
        import websockets  # defer import so module is testable without websockets

        # Reload open positions before connecting
        symbols = self._reload_positions()
        # Record exactly which symbols this session was subscribed to.
        # _symbol_poll_loop must NOT change _open_symbols during the session;
        # instead it sets _needs_reconnect=True when the DB set diverges.
        self._open_symbols = set(symbols)
        self._needs_reconnect = False

        if not symbols:
            logger.debug("markprice: no open positions — sleeping %ss before retry", SYMBOL_POLL_S)
            await asyncio.sleep(SYMBOL_POLL_S)
            return

        url = self._build_ws_url(symbols, self._ws_base_url)
        logger.info("markprice: connecting to %s (%d symbols)", url, len(symbols))

        # Symbol-poll task runs alongside the WS reader
        symbol_poll_task = asyncio.create_task(self._symbol_poll_loop())
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                self._feed_status = "live"
                self._retry_count = 0
                logger.info("markprice: connected — listening for mark prices")

                async for raw in ws:
                    try:
                        await self._handle_message(raw)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.debug("markprice: message parse error: %s", exc)

                    # Check if _symbol_poll_loop detected a DB change
                    if self._needs_reconnect:
                        logger.info("markprice: symbol set changed — reconnecting")
                        break
        finally:
            symbol_poll_task.cancel()
            try:
                await symbol_poll_task
            except asyncio.CancelledError:
                pass

    async def _symbol_poll_loop(self) -> None:
        """Periodically poll the DB for open-symbol changes.

        IMPORTANT: this loop must NOT call _reload_positions() (which mutates
        self._positions and self._open_symbols) — doing so would make both sides
        of the reconnect check identical, so the break never fires.

        Instead it only queries the DB for the current symbol set and compares
        against self._open_symbols (the set that was subscribed at connect time).
        When they differ it sets self._needs_reconnect = True; the async-for
        body in _run_once checks that flag each iteration and breaks.
        """
        conn = self._ensure_ro_conn()
        while True:
            await asyncio.sleep(SYMBOL_POLL_S)
            # Read-only symbol query — does NOT mutate self._positions
            try:
                if conn is None:
                    conn = self._ensure_ro_conn()
                if conn is None:
                    continue
                rows = conn.execute(
                    "SELECT UPPER(symbol) FROM trades WHERE status='open'"
                ).fetchall()
                current_syms: Set[str] = {r[0] for r in rows if r[0]}
            except Exception as exc:
                logger.debug("markprice: symbol poll query failed: %s", exc)
                continue

            if current_syms != self._open_symbols:
                logger.info(
                    "markprice: open symbols changed %s → %s; will reconnect",
                    self._open_symbols,
                    current_syms,
                )
                self._needs_reconnect = True
                # No further mutation — _run_once will break and call
                # _reload_positions() at the top of the next session.

    async def _handle_message(self, raw: str) -> None:
        """Process one WS frame from the Binance combined stream.

        Designed to be resilient: malformed or unrecognised frames are silently
        discarded — they must never crash the WS listener loop.
        """
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return  # silently discard malformed frames
        # Combined stream wraps: {"stream":"btcusdt@markPrice","data":{...}}
        data = msg.get("data") if isinstance(msg, dict) else msg
        if not data:
            return

        event_type = data.get("e")
        if event_type != "markPriceUpdate":
            return

        symbol = str(data.get("s", "")).upper()
        mark_str = data.get("p") or data.get("P")  # 'p' is mark price
        if not symbol or mark_str is None:
            return

        try:
            mark = float(mark_str)
        except (ValueError, TypeError):
            return

        pos = self._positions.get(symbol)
        if pos is None:
            return  # not tracking this symbol

        pos.mark_price = mark
        pos.upnl = pos.compute_upnl(mark)
        pos.ts = time.time()
        self._last_mark_ts = time.monotonic()

        await self._maybe_broadcast()

    async def _maybe_broadcast(self, force: bool = False) -> None:
        """Broadcast upnl payload if throttle window has elapsed (or forced)."""
        now = time.monotonic()
        if not force and (now - self._last_broadcast_ts) < BROADCAST_MIN_INTERVAL_S:
            return
        self._last_broadcast_ts = now
        try:
            payload = self.get_upnl_payload()
            await self._broadcast_fn(payload)
        except Exception as exc:
            logger.debug("markprice: broadcast error: %s", exc)

    def close(self) -> None:
        """Release the persistent RO DB connection."""
        if self._ro_conn is not None:
            try:
                self._ro_conn.close()
            except Exception:
                pass
            self._ro_conn = None


# ---------------------------------------------------------------------------
# Module-level singleton (created by lifespan in api/main.py)
# ---------------------------------------------------------------------------

_client: Optional[MarkPriceClient] = None


def get_client() -> Optional[MarkPriceClient]:
    return _client


def create_client(db_path: str, broadcast_fn: Any, ws_base_url: Optional[str] = None) -> MarkPriceClient:
    global _client
    _client = MarkPriceClient(db_path=db_path, broadcast_fn=broadcast_fn, ws_base_url=ws_base_url)
    return _client
