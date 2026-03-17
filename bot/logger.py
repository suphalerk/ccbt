"""Structured logging and SQLite trade journal."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


class JsonFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record as a JSON string."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
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

    def __init__(self, db_path: str = "trades.db") -> None:
        """Initialize the trade journal.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Create the trades table if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
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
                    duration_seconds INTEGER
                )
                """
            )

    def log_trade_open(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        size: float,
        stop_loss: float,
        take_profit: float,
    ) -> int:
        """Log a new trade opening.

        Args:
            symbol: Trading pair.
            side: 'buy' or 'sell'.
            entry_price: Entry price.
            size: Position size.
            stop_loss: Stop loss price.
            take_profit: Take profit price.

        Returns:
            Trade ID.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (timestamp, symbol, side, entry_price, size,
                                    stop_loss, take_profit, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                """,
                (
                    datetime.utcnow().isoformat(),
                    symbol,
                    side,
                    entry_price,
                    size,
                    stop_loss,
                    take_profit,
                ),
            )
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE trades SET exit_price = ?, pnl = ?, pnl_pct = ?,
                                  status = 'closed', close_reason = ?,
                                  duration_seconds = ?
                WHERE id = ?
                """,
                (exit_price, pnl, pnl_pct, close_reason, duration_seconds, trade_id),
            )

    def get_daily_pnl(self, date: Optional[str] = None) -> float:
        """Get total PnL for a given date.

        Args:
            date: Date string (YYYY-MM-DD). Defaults to today.

        Returns:
            Total PnL for the date.
        """
        if date is None:
            date = datetime.utcnow().strftime("%Y-%m-%d")
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute(
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
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_open_trades(self) -> list[dict]:
        """Get all currently open trades.

        Returns:
            List of open trade dictionaries.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'open'"
            ).fetchall()
            return [dict(row) for row in rows]
