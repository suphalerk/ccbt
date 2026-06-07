"""N6 — /api/candles endpoint.

Reads persisted OHLCV from the bot_ohlcv table (written by the bot when it fetches
candles). Returns {available: false} gracefully when the table is absent or empty.

RULES:
- NEVER imports or constructs a ccxt client.
- NEVER calls the exchange or hits the network.
- DB access is read-only (mode=ro, query_only=1 via deps.get_db_path).
- All financial math stays in Python; TS only renders.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query

from api.deps import get_db_path
from api.models import CandleBar, CandlesResponse

router = APIRouter(prefix="/api", tags=["candles"])


def _read_candles(
    db_path: str,
    symbol: Optional[str],
    timeframe: Optional[str],
    limit: int,
) -> CandlesResponse:
    """Read persisted OHLCV from bot_ohlcv table.

    Returns CandlesResponse with available=False when the table is absent or empty.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError:
        # DB does not exist
        return CandlesResponse(available=False, symbol=symbol, timeframe=None, candles=[])

    try:
        # Check if bot_ohlcv table exists
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_ohlcv'"
        )
        if cur.fetchone() is None:
            return CandlesResponse(available=False, symbol=symbol, timeframe=None, candles=[])

        # Build query — filter by symbol and optionally timeframe
        params: List[Any] = []
        where_clauses: List[str] = []
        if symbol:
            where_clauses.append("symbol = ?")
            params.append(symbol)
        if timeframe:
            where_clauses.append("timeframe = ?")
            params.append(timeframe)

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # Determine which timeframe we're returning (the most recent one for the symbol)
        if symbol and not timeframe:
            tf_cur = conn.execute(
                f"SELECT timeframe FROM bot_ohlcv {where_sql} ORDER BY ts DESC LIMIT 1",
                params,
            )
            tf_row = tf_cur.fetchone()
            if tf_row is not None:
                effective_tf = tf_row[0]
                where_clauses.append("timeframe = ?")
                params.append(effective_tf)
                where_sql = "WHERE " + " AND ".join(where_clauses)
            else:
                effective_tf = None
        else:
            effective_tf = timeframe

        # Count rows
        count_cur = conn.execute(
            f"SELECT COUNT(*) FROM bot_ohlcv {where_sql}", params
        )
        total = count_cur.fetchone()[0]
        if total == 0:
            return CandlesResponse(available=False, symbol=symbol, timeframe=None, candles=[])

        # Fetch the N most-recent rows ordered by ts DESC, then reverse for ascending output
        rows_cur = conn.execute(
            f"""
            SELECT ts, open, high, low, close, volume, ema9, ema21, rsi14
            FROM bot_ohlcv
            {where_sql}
            ORDER BY ts DESC
            LIMIT ?
            """,
            params + [limit],
        )
        rows = list(rows_cur.fetchall())
        # Reverse to ascending order (oldest first)
        rows.reverse()

        candles = [
            CandleBar(
                ts=int(row["ts"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                ema9=float(row["ema9"]) if row["ema9"] is not None else None,
                ema21=float(row["ema21"]) if row["ema21"] is not None else None,
                rsi14=float(row["rsi14"]) if row["rsi14"] is not None else None,
            )
            for row in rows
        ]

        return CandlesResponse(
            available=True,
            symbol=symbol,
            timeframe=effective_tf,
            candles=candles,
        )
    except sqlite3.OperationalError:
        return CandlesResponse(available=False, symbol=symbol, timeframe=None, candles=[])
    finally:
        conn.close()


@router.get("/candles", response_model=CandlesResponse)
async def get_candles(
    symbol: Optional[str] = Query(default=None),
    timeframe: Optional[str] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    db: str = Depends(get_db_path),
) -> CandlesResponse:
    """Persisted OHLCV candles + indicators for a symbol.

    Returns {available: false} when no candles are persisted.
    The bot writes to bot_ohlcv whenever it fetches OHLCV from the exchange.
    The dashboard NEVER calls the exchange directly.
    """
    return _read_candles(db_path=db, symbol=symbol, timeframe=timeframe, limit=limit)
