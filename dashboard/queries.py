"""SQLite helper functions to query trades.db for the dashboard."""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# Default database path relative to project root
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "trades.db"


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get a SQLite connection with row factory enabled.

    Args:
        db_path: Path to the database file. Defaults to trades.db in project root.

    Returns:
        sqlite3.Connection with Row factory.
    """
    path = db_path or str(DEFAULT_DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def db_exists(db_path: Optional[str] = None) -> bool:
    """Check if the database file exists and has the trades table.

    Args:
        db_path: Path to the database file.

    Returns:
        True if the database and trades table exist.
    """
    path = db_path or str(DEFAULT_DB_PATH)
    if not Path(path).exists():
        return False
    try:
        with sqlite3.connect(path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
            )
            return cursor.fetchone() is not None
    except Exception:
        return False


def get_recent_trades(limit: int = 50, db_path: Optional[str] = None) -> pd.DataFrame:
    """Get recent trades ordered by most recent first.

    Args:
        limit: Maximum number of trades to return.
        db_path: Path to the database file.

    Returns:
        DataFrame with trade records.
    """
    if not db_exists(db_path):
        return pd.DataFrame()

    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
            conn,
            params=(limit,),
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_closed_trades(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get all closed trades for performance analysis.

    Args:
        db_path: Path to the database file.

    Returns:
        DataFrame with closed trade records.
    """
    if not db_exists(db_path):
        return pd.DataFrame()

    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM trades WHERE status = 'closed' ORDER BY id ASC",
            conn,
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_open_trades(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get all currently open trades.

    Args:
        db_path: Path to the database file.

    Returns:
        DataFrame with open trade records.
    """
    if not db_exists(db_path):
        return pd.DataFrame()

    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY id DESC",
            conn,
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_trade_stats(db_path: Optional[str] = None) -> dict:
    """Compute aggregate trade statistics from closed trades.

    Args:
        db_path: Path to the database file.

    Returns:
        Dictionary with: total_trades, wins, losses, win_rate, profit_factor,
        avg_win, avg_loss, max_drawdown, sharpe_ratio, total_pnl.
    """
    import numpy as np

    closed = get_closed_trades(db_path)

    stats = {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "max_drawdown": 0.0,
        "max_drawdown_pct": 0.0,
        "sharpe_ratio": 0.0,
        "total_pnl": 0.0,
    }

    if closed.empty or "pnl" not in closed.columns:
        return stats

    pnls = closed["pnl"].dropna().values
    if len(pnls) == 0:
        return stats

    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    stats["total_trades"] = len(pnls)
    stats["wins"] = len(wins)
    stats["losses"] = len(losses)
    stats["win_rate"] = len(wins) / len(pnls) if len(pnls) > 0 else 0.0
    stats["total_pnl"] = float(np.sum(pnls))

    gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0.0
    gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 0.0
    stats["profit_factor"] = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    stats["avg_win"] = float(np.mean(wins)) if len(wins) > 0 else 0.0
    stats["avg_loss"] = float(np.mean(losses)) if len(losses) > 0 else 0.0

    # Max drawdown
    cumulative = np.cumsum(pnls)
    peak = np.maximum.accumulate(cumulative)
    drawdown = peak - cumulative
    stats["max_drawdown"] = float(np.max(drawdown)) if len(drawdown) > 0 else 0.0
    peak_at_max_dd = float(peak[np.argmax(drawdown)]) if len(drawdown) > 0 else 1.0
    stats["max_drawdown_pct"] = (
        stats["max_drawdown"] / peak_at_max_dd if peak_at_max_dd > 0 else 0.0
    )

    # Sharpe ratio
    pnl_pcts = closed["pnl_pct"].dropna().values
    if len(pnl_pcts) > 1:
        mean_ret = np.mean(pnl_pcts)
        std_ret = np.std(pnl_pcts, ddof=1)
        stats["sharpe_ratio"] = (
            float(mean_ret / std_ret * np.sqrt(365)) if std_ret > 0 else 0.0
        )

    return stats


def get_equity_curve(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get cumulative PnL over time for equity curve plotting.

    Args:
        db_path: Path to the database file.

    Returns:
        DataFrame with columns: timestamp, pnl, cumulative_pnl.
    """
    closed = get_closed_trades(db_path)
    if closed.empty or "pnl" not in closed.columns:
        return pd.DataFrame(columns=["timestamp", "pnl", "cumulative_pnl"])

    df = closed[["timestamp", "pnl"]].copy()
    df["pnl"] = df["pnl"].fillna(0.0)
    df["cumulative_pnl"] = df["pnl"].cumsum()
    return df


def get_ai_decisions(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get all trades/records that have AI decision data.

    Args:
        db_path: Path to the database file.

    Returns:
        DataFrame with AI decision records.
    """
    if not db_exists(db_path):
        return pd.DataFrame()

    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT id, timestamp, symbol, side, entry_price, exit_price,
                   pnl, pnl_pct, status, ai_decision, ai_confidence,
                   ai_reasoning, ai_override
            FROM trades
            WHERE ai_decision IS NOT NULL
            ORDER BY id DESC
            """,
            conn,
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_daily_pnl(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get PnL aggregated by day.

    Args:
        db_path: Path to the database file.

    Returns:
        DataFrame with columns: date, daily_pnl, trade_count.
    """
    if not db_exists(db_path):
        return pd.DataFrame(columns=["date", "daily_pnl", "trade_count"])

    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT DATE(timestamp) as date,
                   COALESCE(SUM(pnl), 0) as daily_pnl,
                   COUNT(*) as trade_count
            FROM trades
            WHERE status = 'closed'
            GROUP BY DATE(timestamp)
            ORDER BY date ASC
            """,
            conn,
        )
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "daily_pnl", "trade_count"])
    finally:
        conn.close()


def get_today_pnl(db_path: Optional[str] = None) -> float:
    """Get today's total PnL.

    Args:
        db_path: Path to the database file.

    Returns:
        Today's PnL in USDT.
    """
    if not db_exists(db_path):
        return 0.0

    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE timestamp LIKE ? AND status = 'closed'
            """,
            (f"{today}%",),
        ).fetchone()
        return float(row["total_pnl"]) if row else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_today_trade_count(db_path: Optional[str] = None) -> int:
    """Get today's trade count.

    Args:
        db_path: Path to the database file.

    Returns:
        Number of trades today.
    """
    if not db_exists(db_path):
        return 0

    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) as cnt
            FROM trades
            WHERE timestamp LIKE ? AND status = 'closed'
            """,
            (f"{today}%",),
        ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def get_calibration_data(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get AI calibration tracker data for accuracy visualization.

    Args:
        db_path: Path to the database file.

    Returns:
        DataFrame with calibration records.
    """
    path = db_path or str(DEFAULT_DB_PATH)
    if not Path(path).exists():
        return pd.DataFrame()

    try:
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            df = pd.read_sql_query(
                """
                SELECT id, timestamp, symbol, side, entry_price,
                       stated_confidence, position_size_modifier,
                       sl_adjustment, tp_adjustment, market_regime,
                       reasoning, risk_flags, should_skip,
                       outcome, pnl, was_correct
                FROM ai_calibration
                ORDER BY id DESC
                """,
                conn,
            )
            return df
    except Exception:
        return pd.DataFrame()


def get_calibration_stats(db_path: Optional[str] = None) -> dict:
    """Get aggregate calibration statistics.

    Args:
        db_path: Path to the database file.

    Returns:
        Dict with accuracy, regime breakdown, confidence calibration.
    """
    data = get_calibration_data(db_path)
    stats = {
        "total_decisions": 0,
        "decided_trades": 0,
        "accuracy": 0.0,
        "avg_stated_confidence": 0.0,
        "avg_size_modifier": 0.0,
        "regime_accuracy": {},
        "influence_multiplier": 1.0,
    }

    if data.empty:
        return stats

    stats["total_decisions"] = len(data)
    decided = data[data["outcome"].notna() & (data["should_skip"] == 0)]
    stats["decided_trades"] = len(decided)

    if not decided.empty:
        correct = decided["was_correct"].sum()
        stats["accuracy"] = correct / len(decided) if len(decided) > 0 else 0.0
        stats["avg_stated_confidence"] = decided["stated_confidence"].mean()
        stats["avg_size_modifier"] = decided["position_size_modifier"].mean()

        # Accuracy by regime
        for regime in decided["market_regime"].dropna().unique():
            regime_data = decided[decided["market_regime"] == regime]
            if len(regime_data) >= 3:
                regime_correct = regime_data["was_correct"].sum()
                stats["regime_accuracy"][regime] = {
                    "accuracy": regime_correct / len(regime_data),
                    "count": len(regime_data),
                }

        # Influence multiplier
        acc = stats["accuracy"]
        if acc < 0.45:
            stats["influence_multiplier"] = 0.5
        elif acc < 0.55:
            stats["influence_multiplier"] = 0.75
        elif acc < 0.65:
            stats["influence_multiplier"] = 1.0
        else:
            stats["influence_multiplier"] = 1.25

    return stats


def get_consecutive_losses(db_path: Optional[str] = None) -> int:
    """Count consecutive losses from the most recent trades.

    Args:
        db_path: Path to the database file.

    Returns:
        Number of consecutive recent losses.
    """
    closed = get_closed_trades(db_path)
    if closed.empty or "pnl" not in closed.columns:
        return 0

    count = 0
    for pnl in reversed(closed["pnl"].dropna().tolist()):
        if pnl < 0:
            count += 1
        else:
            break
    return count
