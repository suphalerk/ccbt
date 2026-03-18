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
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
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
                    duration_seconds INTEGER,
                    ai_decision TEXT,
                    ai_confidence REAL,
                    ai_reasoning TEXT,
                    ai_risk_flags TEXT,
                    ai_override INTEGER DEFAULT 0
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
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (timestamp, symbol, side, entry_price, size,
                                    stop_loss, take_profit, status,
                                    ai_decision, ai_confidence, ai_reasoning,
                                    ai_risk_flags, ai_override)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
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
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO trades (timestamp, symbol, side, entry_price, size,
                                    stop_loss, take_profit, status,
                                    ai_decision, ai_confidence, ai_reasoning,
                                    ai_risk_flags, ai_override)
                VALUES (?, ?, ?, ?, 0, 0, 0, 'ai_skipped', ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
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
            return cursor.lastrowid  # type: ignore[return-value]

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

    def get_recent_results(self, limit: int = 10) -> list[float]:
        """Get PnL values of recent closed trades.

        Args:
            limit: Maximum number of results.

        Returns:
            List of PnL values (positive=win, negative=loss), most recent first.
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT pnl FROM trades
                WHERE status = 'closed' AND pnl IS NOT NULL
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [row[0] for row in rows]


class CalibrationTracker:
    """Tracks AI advisor accuracy and builds calibration curves.

    Stores every AI decision with its outcome to enable:
    - Rolling accuracy calculation per confidence bucket
    - Automatic influence adjustment based on performance
    - Calibration curve: maps stated confidence to actual win rate
    """

    def __init__(self, db_path: str = "trades.db") -> None:
        """Initialize calibration tracker.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._init_calibration_table()

    def _init_calibration_table(self) -> None:
        """Create the ai_calibration table if it doesn't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
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
                    was_correct INTEGER
                )
                """
            )

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
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO ai_calibration
                    (timestamp, symbol, side, entry_price, stated_confidence,
                     position_size_modifier, sl_adjustment, tp_adjustment,
                     market_regime, reasoning, risk_flags, should_skip)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
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
            return cursor.lastrowid  # type: ignore[return-value]

    def record_outcome(
        self,
        calibration_id: int,
        outcome: str,
        pnl: float,
    ) -> None:
        """Record the outcome of a previously recorded AI decision.

        Args:
            calibration_id: ID from record_decision.
            outcome: "win", "loss", or "breakeven".
            pnl: Actual profit/loss.
        """
        was_correct = 1 if pnl > 0 else 0
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE ai_calibration
                SET outcome = ?, pnl = ?, was_correct = ?
                WHERE id = ?
                """,
                (outcome, pnl, was_correct, calibration_id),
            )

    def get_rolling_accuracy(self, window: int = 30) -> Optional[float]:
        """Get rolling accuracy over the last N decided trades.

        Args:
            window: Number of recent trades to consider.

        Returns:
            Accuracy as float (0-1), or None if insufficient data.
        """
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
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

        - Accuracy < 45%: reduce influence to 0.5 (AI is hurting)
        - Accuracy 45-55%: neutral influence at 0.75 (AI is noise)
        - Accuracy 55-65%: standard influence at 1.0 (AI is helping)
        - Accuracy > 65%: increased influence at 1.25 (AI is adding alpha)

        Args:
            window: Number of recent trades.

        Returns:
            Influence multiplier (0.5 to 1.25).
        """
        accuracy = self.get_rolling_accuracy(window)
        if accuracy is None:
            return 1.0  # Default to standard influence with no data

        if accuracy < 0.45:
            return 0.5
        elif accuracy < 0.55:
            return 0.75
        elif accuracy < 0.65:
            return 1.0
        else:
            return 1.25

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
        with sqlite3.connect(self.db_path) as conn:
            # Get outcomes for this confidence bucket (0.1 width)
            bucket_low = max(0.0, stated_confidence - 0.1)
            bucket_high = min(1.0, stated_confidence + 0.1)

            rows = conn.execute(
                """
                SELECT was_correct FROM ai_calibration
                WHERE outcome IS NOT NULL
                  AND should_skip = 0
                  AND stated_confidence BETWEEN ? AND ?
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
        with sqlite3.connect(self.db_path) as conn:
            # Overall accuracy (last 30)
            rows = conn.execute(
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
        with sqlite3.connect(self.db_path) as conn2:
            regime_rows = conn2.execute(
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
