"""Structured logging and SQLite trade journal."""

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# pandas is imported lazily inside upsert_candles() so the journal module
# remains importable in stdlib-only environments (e.g. lightweight health checks).
# All type annotations that reference pd.* use string forward-references.

# ---------------------------------------------------------------------------
# bot_ohlcv producer constants
# ---------------------------------------------------------------------------

#: Maximum number of candle rows retained per (symbol, timeframe) pair.
#: Old rows are pruned so the table stays bounded.
MAX_OHLCV_ROWS: int = 200

_BOT_OHLCV_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS bot_ohlcv (
    symbol    TEXT    NOT NULL,
    timeframe TEXT    NOT NULL,
    ts        INTEGER NOT NULL,   -- unix epoch ms (candle open time)
    open      REAL    NOT NULL,
    high      REAL    NOT NULL,
    low       REAL    NOT NULL,
    close     REAL    NOT NULL,
    volume    REAL    NOT NULL,
    ema9      REAL,               -- mapped from ema_fast
    ema21     REAL,               -- mapped from ema_slow
    rsi14     REAL,               -- mapped from rsi
    PRIMARY KEY (symbol, timeframe, ts)
)
"""


def _ensure_bot_ohlcv_table(conn: sqlite3.Connection) -> None:
    """Create bot_ohlcv table if not already present (idempotent)."""
    conn.execute(_BOT_OHLCV_CREATE_SQL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_ohlcv_sym_tf_ts "
        "ON bot_ohlcv (symbol, timeframe, ts)"
    )


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply standard WAL-mode pragmas to a SQLite connection.

    Centralises the busy_timeout, journal_mode, and synchronous settings so
    every persistent or transient connection to trades.db uses the same
    configuration.  Call this on every new connection *before* any DML.

    Args:
        conn: An open sqlite3.Connection to configure.
    """
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

# Bangkok timezone (GMT+7)
_TZ_BKK = timezone(timedelta(hours=7))


class JsonFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a JSON string."""
        log_entry = {
            "timestamp": datetime.now(_TZ_BKK).strftime("%Y-%m-%d %H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        if hasattr(record, "__dict__"):
            extras = {
                k: v
                for k, v in record.__dict__.items()
                if k not in logging.LogRecord.__dict__
                and k
                not in (
                    "name",
                    "msg",
                    "args",
                    "created",
                    "filename",
                    "funcName",
                    "levelname",
                    "levelno",
                    "lineno",
                    "module",
                    "msecs",
                    "pathname",
                    "process",
                    "processName",
                    "relativeCreated",
                    "stack_info",
                    "thread",
                    "threadName",
                    "exc_info",
                    "exc_text",
                    "message",
                    "taskName",
                )
            }
            if extras:
                log_entry["data"] = extras
        return json.dumps(log_entry)


def setup_logging(log_file: Optional[str] = "trading_bot.log") -> None:
    """Configure structured JSON logging.

    Args:
        log_file: Path to the log file. None for console only.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(JsonFormatter())
    root_logger.addHandler(console_handler)

    # File handler
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(JsonFormatter())
        root_logger.addHandler(file_handler)


class TradeJournal:
    """SQLite-based trade journal for logging and tracking trades."""

    def __init__(self, db_path: str = None) -> None:
        """Initialize the trade journal.

        Opens a persistent connection for the lifetime of the instance.
        Uses check_same_thread=False because asyncio may resume coroutines
        on a different OS thread while still being single-threaded logically.

        Args:
            db_path: Path to the SQLite database file.
                     Defaults to {BOT_DATA_DIR}/trades.db or ./trades.db
        """
        if db_path is None:
            data_dir = os.getenv("BOT_DATA_DIR", "")
            db_path = str(Path(data_dir) / "trades.db") if data_dir else "trades.db"
        self.db_path = db_path
        # Persistent connection — avoids per-query connect/disconnect overhead
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def close(self) -> None:
        """Close the persistent database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def _init_db(self) -> None:
        """Create the trades table, bot_health table, and indexes if they don't exist."""
        _apply_pragmas(self._conn)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL UNIQUE,
                config_file TEXT,
                strategy TEXT,
                mode TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'starting',
                last_heartbeat TEXT,
                last_signal TEXT,
                last_signal_time TEXT,
                position_side TEXT,
                position_size REAL DEFAULT 0,
                position_entry REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                last_error TEXT,
                last_error_time TEXT,
                loop_count INTEGER DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                started_at TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL,
                size REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                pnl REAL,
                pnl_pct REAL,
                status TEXT NOT NULL DEFAULT 'open',
                close_reason TEXT,
                duration_seconds INTEGER,
                ai_decision TEXT,
                ai_confidence REAL,
                ai_reasoning TEXT,
                ai_risk_flags TEXT,
                ai_override INTEGER DEFAULT 0
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)"
        )
        # Non-destructive migration: add close_timestamp column if it doesn't exist.
        # NULL for pre-existing rows — get_today_realized_by_close() falls back to
        # the open timestamp for NULL rows.
        existing_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(trades)").fetchall()
        }
        if "close_timestamp" not in existing_cols:
            self._conn.execute(
                "ALTER TABLE trades ADD COLUMN close_timestamp TEXT"
            )
        # Candle producer table — created here so the table exists from bot startup
        # even before the first upsert_candles() call.
        _ensure_bot_ohlcv_table(self._conn)
        self._conn.commit()

    def log_trade_open(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        size: float,
        stop_loss: float,
        take_profit: float,
        ai_decision: Optional[str] = None,
        ai_confidence: Optional[float] = None,
        ai_reasoning: Optional[str] = None,
        ai_risk_flags: Optional[list[str]] = None,
        ai_override: bool = False,
    ) -> int:
        """Log a new trade opening.

        Args:
            symbol: Trading pair.
            side: 'buy' or 'sell'.
            entry_price: Entry price.
            size: Position size.
            stop_loss: Stop loss price.
            take_profit: Take profit price.
            ai_decision: AI analyst decision (execute/skip/wait).
            ai_confidence: AI confidence score (0-1).
            ai_reasoning: AI reasoning text.
            ai_risk_flags: List of risk flags from AI.
            ai_override: Whether AI overrode the signal.

        Returns:
            Trade ID.
        """
        flags_json = json.dumps(ai_risk_flags) if ai_risk_flags else None
        cursor = self._conn.execute(
            """
            INSERT INTO trades (timestamp, symbol, side, entry_price, size,
                                stop_loss, take_profit, status,
                                ai_decision, ai_confidence, ai_reasoning,
                                ai_risk_flags, ai_override)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                symbol,
                side,
                entry_price,
                size,
                stop_loss,
                take_profit,
                ai_decision,
                ai_confidence,
                ai_reasoning,
                flags_json,
                1 if ai_override else 0,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def log_trade_close(
        self,
        trade_id: int,
        exit_price: float,
        pnl: float,
        pnl_pct: float,
        close_reason: str,
        duration_seconds: int,
    ) -> None:
        """Log a trade closing.

        Args:
            trade_id: ID of the trade to close.
            exit_price: Exit price.
            pnl: Profit/loss in USDT.
            pnl_pct: Profit/loss percentage.
            close_reason: Reason for closing (tp, sl, trailing_sl, manual, circuit_breaker).
            duration_seconds: Trade duration in seconds.
        """
        close_ts = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            UPDATE trades SET exit_price = ?, pnl = ?, pnl_pct = ?,
                              status = 'closed', close_reason = ?,
                              duration_seconds = ?,
                              close_timestamp = ?
            WHERE id = ?
            """,
            (exit_price, pnl, pnl_pct, close_reason, duration_seconds, close_ts, trade_id),
        )
        self._conn.commit()

    def get_daily_pnl(self, date: Optional[str] = None) -> float:
        """Get total PnL for a given date.

        Args:
            date: Date string (YYYY-MM-DD). Defaults to today.

        Returns:
            Total PnL for the date.
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        result = self._conn.execute(
            """
            SELECT COALESCE(SUM(pnl), 0)
            FROM trades
            WHERE timestamp LIKE ? AND status = 'closed'
            """,
            (f"{date}%",),
        ).fetchone()
        return result[0]

    def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Get recent trade history.

        Args:
            limit: Maximum number of trades to return.

        Returns:
            List of trade dictionaries.
        """
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        self._conn.row_factory = None
        return [dict(row) for row in rows]

    def log_ai_decision(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        ai_decision: str,
        ai_confidence: float,
        ai_reasoning: str,
        ai_risk_flags: Optional[list[str]] = None,
        ai_override: bool = False,
    ) -> int:
        """Log an AI decision, including skipped signals.

        Records AI decisions even when the trade is not executed,
        enabling later analysis of skip accuracy.

        Args:
            symbol: Trading pair.
            side: Signal direction.
            entry_price: Candidate entry price.
            ai_decision: AI decision (execute/skip/wait).
            ai_confidence: AI confidence score.
            ai_reasoning: AI reasoning text.
            ai_risk_flags: Risk flags from AI.
            ai_override: Whether AI overrode the signal.

        Returns:
            Record ID.
        """
        flags_json = json.dumps(ai_risk_flags) if ai_risk_flags else None
        cursor = self._conn.execute(
            """
            INSERT INTO trades (timestamp, symbol, side, entry_price, size,
                                stop_loss, take_profit, status,
                                ai_decision, ai_confidence, ai_reasoning,
                                ai_risk_flags, ai_override)
            VALUES (?, ?, ?, ?, 0, 0, 0, 'ai_skipped', ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                symbol,
                side,
                entry_price,
                ai_decision,
                ai_confidence,
                ai_reasoning,
                flags_json,
                1 if ai_override else 0,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def get_open_trades(self) -> list[dict]:
        """Get all currently open trades.

        Returns:
            List of open trade dictionaries.
        """
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE status = 'open'"
        ).fetchall()
        self._conn.row_factory = None
        return [dict(row) for row in rows]

    def get_recent_results(self, limit: int = 10) -> list[float]:
        """Get PnL values of recent closed trades.

        Args:
            limit: Maximum number of results.

        Returns:
            List of PnL values (positive=win, negative=loss), most recent first.
        """
        rows = self._conn.execute(
            """
            SELECT pnl FROM trades
            WHERE status = 'closed' AND pnl IS NOT NULL
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [row[0] for row in rows]

    def upsert_health(self, symbol: str, **kwargs) -> None:
        """Insert or update bot health status. Called every loop iteration.

        Uses INSERT OR REPLACE on the symbol UNIQUE constraint so every call
        is an upsert.  All extra keyword arguments are treated as optional
        columns; unknown keys are silently ignored.

        Args:
            symbol: Trading symbol (e.g. 'BTC/USDT:USDT').
            **kwargs: Any subset of bot_health columns.
        """
        now_bkk = datetime.now(_TZ_BKK).strftime("%Y-%m-%d %H:%M:%S")
        kwargs.setdefault("updated_at", now_bkk)

        # On first insert, record started_at if not already in DB
        existing = self._conn.execute(
            "SELECT started_at FROM bot_health WHERE symbol = ?", (symbol,)
        ).fetchone()
        if existing is None:
            kwargs.setdefault("started_at", now_bkk)

        # Build column list from allowed schema columns (avoid SQL injection)
        _ALLOWED_COLS = {
            "config_file", "strategy", "mode", "status",
            "last_heartbeat", "last_signal", "last_signal_time",
            "position_side", "position_size", "position_entry",
            "unrealized_pnl", "error_count", "last_error", "last_error_time",
            "loop_count", "total_trades", "total_pnl", "started_at", "updated_at",
        }
        cols = {k: v for k, v in kwargs.items() if k in _ALLOWED_COLS}
        cols["updated_at"] = now_bkk  # always refresh

        # Merge with existing row so INSERT OR REPLACE doesn't wipe unchanged cols
        if existing is not None:
            # Fetch full row to preserve untouched columns
            row = self._conn.execute(
                "SELECT * FROM bot_health WHERE symbol = ?", (symbol,)
            ).fetchone()
            if row:
                col_names = [desc[0] for desc in self._conn.execute(
                    "SELECT * FROM bot_health WHERE symbol = ?", (symbol,)
                ).description or []]
                # Re-fetch with description
                cur = self._conn.execute(
                    "SELECT * FROM bot_health WHERE symbol = ?", (symbol,)
                )
                col_names = [d[0] for d in cur.description]
                existing_row = dict(zip(col_names, cur.fetchone()))
                existing_row.update(cols)
                cols = {k: v for k, v in existing_row.items() if k != "id"}

        all_cols = ["symbol"] + list(cols.keys())
        all_vals = [symbol] + list(cols.values())
        placeholders = ", ".join("?" * len(all_cols))
        col_str = ", ".join(all_cols)

        self._conn.execute(
            f"INSERT OR REPLACE INTO bot_health ({col_str}) VALUES ({placeholders})",
            all_vals,
        )
        self._conn.commit()

    def upsert_candles(
        self,
        symbol: str,
        timeframe: str,
        df: "pd.DataFrame",
        max_rows: int = MAX_OHLCV_ROWS,
    ) -> None:
        """Persist OHLCV + indicator snapshot using the persistent connection.

        This is a thin wrapper around the module-level :func:`upsert_candles`
        that passes ``self._conn`` so no new connection is opened per tick.
        Calling it multiple times with the same closed-candle data is safe
        (INSERT OR REPLACE makes writes idempotent).

        Args:
            symbol:    Trading symbol (e.g. ``"BTCUSDT"`` or ``"BTC/USDT:USDT"``).
            timeframe: Candle timeframe string (e.g. ``"15m"``, ``"1h"``).
            df:        DataFrame produced by ``add_indicators()``.
            max_rows:  Maximum rows retained per (symbol, timeframe).
        """
        _upsert_candles_conn(
            conn=self._conn,
            symbol=symbol,
            timeframe=timeframe,
            df=df,
            max_rows=max_rows,
        )


# ---------------------------------------------------------------------------
# Candle producer (N6)
# ---------------------------------------------------------------------------

def _upsert_candles_conn(
    conn: "sqlite3.Connection",
    symbol: str,
    timeframe: str,
    df: "pd.DataFrame",
    max_rows: int = MAX_OHLCV_ROWS,
) -> None:
    """Write candle rows using a caller-supplied connection.

    Extracted so :class:`TradeJournal` can call this with its persistent
    ``self._conn`` (avoiding a new connect/close per tick) while the
    module-level :func:`upsert_candles` still works for one-shot callers.

    Idempotent: INSERT OR REPLACE means duplicate ts values for the same
    (symbol, timeframe) are safe to call multiple times per closed candle.

    Args:
        conn:      Open sqlite3.Connection configured with WAL pragmas.
        symbol:    Trading symbol.
        timeframe: Candle timeframe string.
        df:        DataFrame from ``add_indicators()``.
        max_rows:  Row retention limit per (symbol, timeframe).
    """
    if df is None or len(df) == 0:
        return

    import math
    import pandas as pd  # lazy import — keeps journal importable stdlib-only

    _ensure_bot_ohlcv_table(conn)

    # Resolve the timestamp series.
    # The live-bot DataFrame (from get_ohlcv) uses a DatetimeIndex.
    # Test fixtures may supply a plain integer `timestamp` column.
    if "timestamp" in df.columns:
        # Column-based (test fixtures or already-reset index)
        ts_series: "pd.Series" = df["timestamp"]
        _ts_is_datetime = pd.api.types.is_datetime64_any_dtype(ts_series)
    else:
        # Index-based (live bot — get_ohlcv sets index to DatetimeIndex)
        ts_series = df.index.to_series()
        _ts_is_datetime = True

    def _to_epoch_ms(ts_val) -> int:
        """Convert a timestamp value to integer epoch milliseconds."""
        if _ts_is_datetime:
            # pandas Timestamp / numpy datetime64 → int ns → divide by 1e6
            try:
                return int(pd.Timestamp(ts_val).timestamp() * 1000)
            except Exception:
                return int(ts_val)
        else:
            # Already an integer (epoch ms from test fixture)
            return int(ts_val)

    # Build rows from the DataFrame.
    # Columns may or may not be present depending on config (strategy type).
    def _col(name: str) -> "pd.Series | None":
        return df[name] if name in df.columns else None

    ema_fast_col = _col("ema_fast")
    ema_slow_col = _col("ema_slow")
    rsi_col = _col("rsi")

    def _safe_float(col: "pd.Series | None", idx: int) -> "float | None":
        if col is None:
            return None
        val = col.iloc[idx]
        if val is None:
            return None
        try:
            fval = float(val)
            return None if math.isnan(fval) else fval
        except (TypeError, ValueError):
            return None

    rows = []
    for idx in range(len(df)):
        ts_ms = _to_epoch_ms(ts_series.iloc[idx])
        rows.append((
            symbol,
            timeframe,
            ts_ms,
            float(df["open"].iloc[idx]),
            float(df["high"].iloc[idx]),
            float(df["low"].iloc[idx]),
            float(df["close"].iloc[idx]),
            float(df["volume"].iloc[idx]),
            _safe_float(ema_fast_col, idx),   # ema9
            _safe_float(ema_slow_col, idx),   # ema21
            _safe_float(rsi_col, idx),        # rsi14
        ))

    conn.executemany(
        """
        INSERT OR REPLACE INTO bot_ohlcv
            (symbol, timeframe, ts, open, high, low, close, volume,
             ema9, ema21, rsi14)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )

    # Prune rows beyond max_rows — keep the most-recent ones.
    conn.execute(
        """
        DELETE FROM bot_ohlcv
        WHERE symbol = ? AND timeframe = ?
          AND ts NOT IN (
              SELECT ts FROM bot_ohlcv
              WHERE symbol = ? AND timeframe = ?
              ORDER BY ts DESC
              LIMIT ?
          )
        """,
        (symbol, timeframe, symbol, timeframe, max_rows),
    )

    conn.commit()


def upsert_candles(
    db_path: str,
    symbol: str,
    timeframe: str,
    df: "pd.DataFrame",
    max_rows: int = MAX_OHLCV_ROWS,
) -> None:
    """Persist OHLCV + indicator snapshot from the bot's per-loop DataFrame.

    This is the **only** place the bot writes candle data to SQLite for one-shot
    callers (e.g. scripts, tests).  The :class:`TradeJournal` instance method
    :meth:`TradeJournal.upsert_candles` should be preferred in the trading loop
    because it reuses the persistent connection.

    Column mapping (add_indicators → bot_ohlcv):
        ``ema_fast``  → ``ema9``
        ``ema_slow``  → ``ema21``
        ``rsi``       → ``rsi14``

    The table is created idempotently on the first call.  Old rows beyond
    ``max_rows`` per (symbol, timeframe) are pruned so the table stays bounded.

    Args:
        db_path:   Path to the SQLite trades database.
        symbol:    Trading symbol (e.g. ``"BTCUSDT"`` or ``"BTC/USDT:USDT"``).
        timeframe: Candle timeframe string (e.g. ``"15m"``, ``"1h"``).
        df:        DataFrame produced by ``add_indicators()``.  Must have at
                   minimum: ``timestamp``, ``open``, ``high``, ``low``,
                   ``close``, ``volume``.  ``ema_fast``, ``ema_slow``, ``rsi``
                   are mapped when present; ``None`` is stored when absent.
        max_rows:  Maximum rows retained per (symbol, timeframe). Defaults to
                   :data:`MAX_OHLCV_ROWS`.
    """
    if df is None or len(df) == 0:
        return

    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        _apply_pragmas(conn)
        _upsert_candles_conn(conn=conn, symbol=symbol, timeframe=timeframe,
                             df=df, max_rows=max_rows)
    finally:
        conn.close()


class CalibrationTracker:
    """Tracks AI advisor accuracy and builds calibration curves.

    Stores every AI decision with its outcome to enable:
    - Rolling accuracy calculation per confidence bucket
    - Automatic influence adjustment based on performance
    - Calibration curve: maps stated confidence to actual win rate
    """

    def __init__(self, db_path: str = None) -> None:
        """Initialize calibration tracker.

        Opens a persistent connection for the lifetime of the instance.
        Uses check_same_thread=False because asyncio may resume coroutines
        on a different OS thread while still being single-threaded logically.

        Args:
            db_path: Path to the SQLite database file.
                     Defaults to {BOT_DATA_DIR}/trades.db or ./trades.db
        """
        if db_path is None:
            data_dir = os.getenv("BOT_DATA_DIR", "")
            db_path = str(Path(data_dir) / "trades.db") if data_dir else "trades.db"
        self.db_path = db_path
        # Persistent connection — avoids per-query connect/disconnect overhead
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_calibration_table()

    def close(self) -> None:
        """Close the persistent database connection."""
        try:
            self._conn.close()
        except Exception:
            pass

    def _init_calibration_table(self) -> None:
        """Create the ai_calibration table and indexes if they don't exist."""
        _apply_pragmas(self._conn)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_calibration (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stated_confidence REAL NOT NULL,
                position_size_modifier REAL NOT NULL DEFAULT 1.0,
                sl_adjustment REAL NOT NULL DEFAULT 1.0,
                tp_adjustment REAL NOT NULL DEFAULT 1.0,
                market_regime TEXT,
                reasoning TEXT,
                risk_flags TEXT,
                should_skip INTEGER DEFAULT 0,
                outcome TEXT,
                pnl REAL,
                was_correct INTEGER,
                value_add REAL
            )
            """
        )
        # Add value_add column to existing tables (safe no-op if already present)
        try:
            self._conn.execute(
                "ALTER TABLE ai_calibration ADD COLUMN value_add REAL"
            )
        except Exception:
            pass  # Column already exists
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_calibration_outcome "
            "ON ai_calibration(outcome, should_skip)"
        )
        self._conn.commit()

    def record_decision(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stated_confidence: float,
        position_size_modifier: float = 1.0,
        sl_adjustment: float = 1.0,
        tp_adjustment: float = 1.0,
        market_regime: str = "unknown",
        reasoning: str = "",
        risk_flags: Optional[list[str]] = None,
        should_skip: bool = False,
    ) -> int:
        """Record an AI advisor decision for later calibration.

        Args:
            symbol: Trading pair.
            side: Signal direction.
            entry_price: Entry price.
            stated_confidence: AI's stated confidence (0-1).
            position_size_modifier: AI's position size adjustment.
            sl_adjustment: AI's SL adjustment.
            tp_adjustment: AI's TP adjustment.
            market_regime: AI's regime classification.
            reasoning: AI's reasoning text.
            risk_flags: Risk flags from AI.
            should_skip: Whether AI recommended skipping.

        Returns:
            Record ID.
        """
        flags_json = json.dumps(risk_flags) if risk_flags else None
        cursor = self._conn.execute(
            """
            INSERT INTO ai_calibration
                (timestamp, symbol, side, entry_price, stated_confidence,
                 position_size_modifier, sl_adjustment, tp_adjustment,
                 market_regime, reasoning, risk_flags, should_skip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                symbol,
                side,
                entry_price,
                stated_confidence,
                position_size_modifier,
                sl_adjustment,
                tp_adjustment,
                market_regime,
                reasoning,
                flags_json,
                1 if should_skip else 0,
            ),
        )
        self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    def record_outcome(
        self,
        calibration_id: int,
        outcome: str,
        pnl: float,
        default_pnl: Optional[float] = None,
    ) -> None:
        """Record the outcome of a previously recorded AI decision.

        Args:
            calibration_id: ID from record_decision.
            outcome: "win", "loss", or "breakeven".
            pnl: Actual profit/loss (with AI adjustments applied).
            default_pnl: What PnL would have been WITHOUT AI adjustments.
                         Positive value_add means AI added value.
        """
        was_correct = 1 if pnl > 0 else 0
        value_add = (pnl - default_pnl) if default_pnl is not None else None
        self._conn.execute(
            """
            UPDATE ai_calibration
            SET outcome = ?, pnl = ?, was_correct = ?, value_add = ?
            WHERE id = ?
            """,
            (outcome, pnl, was_correct, value_add, calibration_id),
        )
        self._conn.commit()

    def get_rolling_accuracy(self, window: int = 30) -> Optional[float]:
        """Get rolling accuracy over the last N decided trades.

        Args:
            window: Number of recent trades to consider.

        Returns:
            Accuracy as float (0-1), or None if insufficient data.
        """
        rows = self._conn.execute(
            """
            SELECT was_correct FROM ai_calibration
            WHERE outcome IS NOT NULL AND should_skip = 0
            ORDER BY id DESC LIMIT ?
            """,
            (window,),
        ).fetchall()

        if len(rows) < 5:  # Need minimum 5 trades for meaningful accuracy
            return None

        correct = sum(r[0] for r in rows)
        return correct / len(rows)

    def get_influence_multiplier(self, window: int = 30) -> float:
        """Get the AI influence multiplier based on rolling accuracy.

        Linear scale: 0.3 accuracy -> 0.70, 0.5 -> 1.0, 0.65+ -> 1.225
        Formula: max(0.25, min(1.5, 0.25 + accuracy * 1.5))

        Args:
            window: Number of recent trades.

        Returns:
            Influence multiplier (0.25 to 1.5).
        """
        accuracy = self.get_rolling_accuracy(window)
        if accuracy is None:
            return 1.0  # Default to standard influence with no data

        return max(0.25, min(1.5, 0.25 + accuracy * 1.5))

    def calibrate(self, stated_confidence: float) -> float:
        """Adjust stated confidence using the calibration curve.

        Builds a simple calibration by comparing stated confidence buckets
        to actual win rates. If stated 0.8 confidence only wins 60% of the
        time, the calibrated confidence is adjusted down.

        Args:
            stated_confidence: AI's stated confidence (0-1).

        Returns:
            Calibrated confidence (0-1).
        """
        # Fixed 0.2-wide buckets (non-overlapping)
        bucket_low = max(0.0, (stated_confidence // 0.2) * 0.2)
        bucket_high = min(1.0, bucket_low + 0.2)

        rows = self._conn.execute(
            """
            SELECT was_correct FROM ai_calibration
            WHERE outcome IS NOT NULL
              AND should_skip = 0
              AND stated_confidence >= ? AND stated_confidence < ?
            ORDER BY id DESC LIMIT 20
            """,
            (bucket_low, bucket_high),
        ).fetchall()

        if len(rows) < 5:
            # Not enough data for calibration, return stated confidence
            return stated_confidence

        actual_win_rate = sum(r[0] for r in rows) / len(rows)
        # Blend: 70% actual win rate + 30% stated confidence (shrinkage toward actual)
        calibrated = 0.7 * actual_win_rate + 0.3 * stated_confidence
        return max(0.0, min(1.0, calibrated))

    def get_accuracy_feedback(self) -> str:
        """Generate accuracy feedback string for the system prompt.

        Returns:
            Human-readable accuracy summary, or empty string if insufficient data.
        """
        # Overall accuracy (last 30)
        rows = self._conn.execute(
            """
            SELECT was_correct, stated_confidence FROM ai_calibration
            WHERE outcome IS NOT NULL AND should_skip = 0
            ORDER BY id DESC LIMIT 30
            """,
        ).fetchall()

        if len(rows) < 5:
            return ""

        total = len(rows)
        correct = sum(r[0] for r in rows)
        avg_confidence = sum(r[1] for r in rows) / total

        lines = [
            f"Last {total} trades: {correct} correct, {total - correct} wrong "
            f"({correct / total:.0%} accuracy)",
            f"Your average stated confidence: {avg_confidence:.0%}",
        ]

        # Calibration note
        if correct / total < avg_confidence - 0.1:
            lines.append(
                "WARNING: You are OVERCONFIDENT. Your stated confidence exceeds "
                "actual accuracy. Lower your confidence estimates."
            )
        elif correct / total > avg_confidence + 0.1:
            lines.append(
                "NOTE: You are UNDERCONFIDENT. Your actual accuracy exceeds "
                "stated confidence. You can increase confidence when conviction is high."
            )

        # Per-regime accuracy
        regime_rows = self._conn.execute(
            """
            SELECT market_regime, COUNT(*) as n,
                   SUM(was_correct) as wins
            FROM ai_calibration
            WHERE outcome IS NOT NULL AND should_skip = 0
              AND market_regime IS NOT NULL
            GROUP BY market_regime
            HAVING n >= 3
            """,
        ).fetchall()

        if regime_rows:
            lines.append("Accuracy by regime:")
            for regime, n, wins in regime_rows:
                lines.append(f"  {regime}: {wins}/{n} ({wins / n:.0%})")

        return "\n".join(lines)
