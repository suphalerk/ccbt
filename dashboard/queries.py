"""SQLite helper functions to query trades.db for the dashboard."""

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# Default database path: check BOT_DATA_DIR first (Docker), then project root
_data_dir = os.getenv("BOT_DATA_DIR", "")
if _data_dir and Path(_data_dir, "trades.db").exists():
    DEFAULT_DB_PATH = Path(_data_dir) / "trades.db"
else:
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


def _symbol_filter(symbol: Optional[str] = None) -> tuple[str, tuple]:
    """Build a SQL WHERE clause fragment for optional symbol filtering.

    Returns:
        (sql_fragment, params_tuple) — fragment is empty string when no filter.
    """
    if symbol:
        return " AND symbol = ?", (symbol,)
    return "", ()


def get_distinct_symbols(db_path: Optional[str] = None) -> list[str]:
    """Get all distinct symbols that have traded."""
    if not db_exists(db_path):
        return []
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM trades ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows if r["symbol"]]
    except Exception:
        return []
    finally:
        conn.close()


def get_recent_trades(
    limit: int = 50,
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """Get recent trades ordered by most recent first."""
    if not db_exists(db_path):
        return pd.DataFrame()

    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            f"SELECT * FROM trades WHERE 1=1{filt} ORDER BY id DESC LIMIT ?",
            conn,
            params=params + (limit,),
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_closed_trades(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """Get all closed trades for performance analysis."""
    if not db_exists(db_path):
        return pd.DataFrame()

    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            f"SELECT * FROM trades WHERE status = 'closed' AND COALESCE(close_reason, '') != 'orphan_reconcile'{filt} ORDER BY id ASC",
            conn,
            params=params,
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_open_trades(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """Get all currently open trades."""
    if not db_exists(db_path):
        return pd.DataFrame()

    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            f"SELECT * FROM trades WHERE status = 'open'{filt} ORDER BY id DESC",
            conn,
            params=params,
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def get_trade_stats(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> dict:
    """Compute aggregate trade statistics from closed trades."""
    import numpy as np

    closed = get_closed_trades(db_path, symbol=symbol)

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


def get_equity_curve(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """Get cumulative PnL over time for equity curve plotting."""
    closed = get_closed_trades(db_path, symbol=symbol)
    if closed.empty or "pnl" not in closed.columns:
        return pd.DataFrame(columns=["timestamp", "pnl", "cumulative_pnl"])

    df = closed[["timestamp", "pnl"]].copy()
    df["pnl"] = df["pnl"].fillna(0.0)
    df["cumulative_pnl"] = df["pnl"].cumsum()
    return df


def get_ai_decisions(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get all trades/records that have AI decision data."""
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


def get_daily_pnl(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """Get PnL aggregated by day."""
    if not db_exists(db_path):
        return pd.DataFrame(columns=["date", "daily_pnl", "trade_count"])

    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            f"""
            SELECT DATE(timestamp) as date,
                   COALESCE(SUM(pnl), 0) as daily_pnl,
                   COUNT(*) as trade_count
            FROM trades
            WHERE status = 'closed' AND COALESCE(close_reason, '') != 'orphan_reconcile'{filt}
            GROUP BY DATE(timestamp)
            ORDER BY date ASC
            """,
            conn,
            params=params,
        )
        return df
    except Exception:
        return pd.DataFrame(columns=["date", "daily_pnl", "trade_count"])
    finally:
        conn.close()


def get_today_pnl(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> float:
    """Get today's total PnL."""
    if not db_exists(db_path):
        return 0.0

    today = datetime.utcnow().strftime("%Y-%m-%d")
    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            f"""
            SELECT COALESCE(SUM(pnl), 0) as total_pnl
            FROM trades
            WHERE timestamp LIKE ? AND status = 'closed' AND COALESCE(close_reason, '') != 'orphan_reconcile'{filt}
            """,
            (f"{today}%",) + params,
        ).fetchone()
        return float(row["total_pnl"]) if row else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def get_today_trade_count(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> int:
    """Get today's trade count."""
    if not db_exists(db_path):
        return 0

    today = datetime.utcnow().strftime("%Y-%m-%d")
    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            f"""
            SELECT COUNT(*) as cnt
            FROM trades
            WHERE timestamp LIKE ? AND status = 'closed' AND COALESCE(close_reason, '') != 'orphan_reconcile'{filt}
            """,
            (f"{today}%",) + params,
        ).fetchone()
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0
    finally:
        conn.close()


def get_calibration_data(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get AI calibration tracker data for accuracy visualization."""
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
    """Get aggregate calibration statistics."""
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


def get_recent_logs(
    max_lines: int = 100,
    min_level: str = "ALL",
    search: str = "",
    log_path: Optional[str] = None,
) -> list[dict]:
    """Read recent log entries from the trading bot log file."""
    import json as _json

    level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
    min_rank = level_order.get(min_level.upper(), -1)  # ALL → -1, passes everything

    path = Path(log_path) if log_path else Path(__file__).resolve().parent.parent / "trading_bot.log"
    if not path.exists():
        return []

    # Read last ~64KB from file for efficiency
    try:
        file_size = path.stat().st_size
        if file_size == 0:
            return []

        read_size = min(file_size, 65536)
        with open(path, "rb") as f:
            if file_size > read_size:
                f.seek(file_size - read_size)
                # Skip partial first line
                f.readline()
            raw_lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []

    entries = []
    search_lower = search.lower() if search else ""

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            continue

        level = obj.get("level", "INFO")
        if min_rank >= 0 and level_order.get(level, 0) < min_rank:
            continue

        message = obj.get("message", "")
        data = obj.get("data", {})

        if search_lower:
            haystack = f"{message} {_json.dumps(data)}".lower()
            if search_lower not in haystack:
                continue

        entries.append({
            "timestamp": obj.get("timestamp", ""),
            "level": level,
            "message": message,
            "data": data,
        })

    # Return last max_lines entries, newest first
    return list(reversed(entries[-max_lines:]))


def get_bot_statuses(project_root: Path, max_stale_seconds: float = 600) -> list[dict]:
    """Scan data/heartbeat_* files to determine which bots are alive.

    Also scans config_*.json files to find ALL configured bots, even those
    that have never written a heartbeat.

    Args:
        project_root: Root directory of the project (where config*.json files live).
        max_stale_seconds: Heartbeat age threshold in seconds (default 10 min).

    Returns:
        List of dicts with keys: config_file, symbol, strategy, last_heartbeat,
        age_seconds, status ('running' or 'stopped').
        Sorted: running bots first, then stopped; alphabetical by symbol within each group.
    """
    _STRATEGY_PATTERNS = [
        ("_ichi4htrail", "4H Trail"),
        ("_emaichi4h", "EMA+Ichi 4H"),
        ("_ichist4h", "Ichi+ST 4H"),
        ("_emaribbon", "EMA Ribbon"),
        ("_dualthrust_adx", "DualThrust+ADX"),
        ("_dualthrust", "Dual Thrust"),
        ("_alligator4h", "Alligator 4H"),
        ("_alligator", "Alligator 1H"),
        ("_dualst4h", "Dual ST 4H"),
        ("_dualst", "Dual ST 1H"),
        ("_ichi4h", "Ichi 4H"),
        ("_ichi_adx", "Ichi+ADX"),
        ("_ichi", "Ichi 1H"),
        ("_ribbon_ao", "Ribbon+AO"),
        ("_zscore", "Z-Score"),
        ("_stochmtf", "Stoch MTF"),
        ("_ema", "EMA 15m"),
        ("_supertrend", "Supertrend"),
        ("_volexp", "VolExp"),
        ("_roc", "ROC Momentum"),
        ("_adxdi", "ADX+DI"),
        ("_chop", "Chop+EMA"),
        ("_wif", "EMA 15m"),
        ("_avax", "Ichi 1H"),
        ("_gold", "Gold Forex"),
    ]

    def _detect_strategy(config_stem: str) -> str:
        for suffix, label in _STRATEGY_PATTERNS:
            if config_stem.endswith(suffix):
                return label
        # Bare "config" = BTC EMA 15m
        if config_stem == "config":
            return "EMA 15m"
        return "Unknown"

    data_dir = project_root / "data"
    now = time.time()

    # Build heartbeat map: symbol_clean -> (timestamp, age_seconds)
    heartbeat_map: dict[str, tuple[float, float]] = {}
    if data_dir.exists():
        for hb_file in data_dir.glob("heartbeat_*"):
            symbol_clean = hb_file.name[len("heartbeat_"):]
            try:
                ts = float(hb_file.read_text().strip())
                age = now - ts
                heartbeat_map[symbol_clean] = (ts, age)
            except (ValueError, OSError):
                pass

    # Scan all config*.json files
    results: list[dict] = []
    seen_symbols: set[str] = set()

    for cfg_path in sorted(project_root.glob("config*.json")):
        # Skip non-bot configs and retired/non-deployed configs
        skip_configs = {
            "config_aggressive.json", "config_sniper.json", "config_yolo.json",
            "config_max.json",
            # Retired bots (no edge)
            "config_doge.json",        # DOGE — retired, PF 1.11
            "config_sol_ichi.json",    # SOL — retired, PF 1.17
            # Gold/forex — separate exchange (OANDA), not Binance
            "config_gold.json",
            "config_gold_forex.json",
            # Replaced by upgraded strategies
            "config_arb.json",                  # replaced by config_arbusdt_ichi4h.json
            "config_btc_ichi.json",             # BTC Ichi 1H — not deployed
            "config_1000shibusdt_ichi.json",    # replaced by config_1000shibusdt_ichi4h.json
            "config_trxusdt_ichi.json",         # replaced by config_trxusdt_ichi4htrail.json
            "config_xlmusdt_ichi.json",         # replaced by config_xlmusdt_ichi4htrail.json
            "config_saharausdt_supertrend.json", # replaced by config_saharausdt_ichi4htrail.json
            "config_polusdt_ichi4htrail.json",  # replaced by config_polusdt_ichi.json
        }
        if cfg_path.name in skip_configs:
            continue
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
        except Exception:
            continue

        symbol = cfg.get("symbol")
        if not symbol:
            continue

        # Normalise: BTC/USDT:USDT → BTCUSDT
        symbol_clean = symbol.replace("/", "").replace(":", "")

        # Deduplicate: keep the most specific config per symbol_clean
        if symbol_clean in seen_symbols:
            continue
        seen_symbols.add(symbol_clean)

        strategy = _detect_strategy(cfg_path.stem)

        if symbol_clean in heartbeat_map:
            ts, age = heartbeat_map[symbol_clean]
            # Dynamic stale threshold based on bot timeframe:
            # 15m → 20min, 1h → 75min, 4h → 255min (4h+15min buffer)
            tf = cfg.get("timeframe_signal", "1h")
            if "h" in tf:
                tf_seconds = int(tf.replace("h", "")) * 3600
            elif "m" in tf:
                tf_seconds = int(tf.replace("m", "")) * 60
            else:
                tf_seconds = 900
            bot_stale_threshold = tf_seconds + 300  # timeframe + 5min buffer
            status = "running" if age <= bot_stale_threshold else "stopped"
            last_hb = datetime.utcfromtimestamp(ts)
        else:
            age = float("inf")
            status = "stopped"
            last_hb = None

        results.append({
            "config_file": cfg_path.name,
            "symbol": symbol,
            "symbol_clean": symbol_clean,
            "strategy": strategy,
            "last_heartbeat": last_hb,
            "age_seconds": age,
            "status": status,
        })

    # Sort: running first, then stopped; within each group alphabetical by symbol
    results.sort(key=lambda r: (0 if r["status"] == "running" else 1, r["symbol"]))
    return results


def get_bot_health(db_path: Optional[str] = None) -> pd.DataFrame:
    """Read bot_health table for rich status display.

    Returns DataFrame with columns: symbol, strategy, mode, status,
    last_heartbeat, position_side, position_size, position_entry,
    error_count, loop_count, total_trades, total_pnl, updated_at.

    If the table doesn't exist (old DB), returns empty DataFrame with
    the expected columns so callers can always rely on the schema.
    """
    _EXPECTED_COLS = [
        "symbol", "strategy", "mode", "status",
        "last_heartbeat", "position_side", "position_size", "position_entry",
        "error_count", "loop_count", "total_trades", "total_pnl", "updated_at",
    ]

    path = db_path or str(DEFAULT_DB_PATH)
    if not Path(path).exists():
        return pd.DataFrame(columns=_EXPECTED_COLS)

    try:
        with sqlite3.connect(path) as conn:
            # Check table exists first to avoid noisy exception on old DBs
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_health'"
            )
            if cursor.fetchone() is None:
                return pd.DataFrame(columns=_EXPECTED_COLS)

            df = pd.read_sql_query(
                """
                SELECT symbol, strategy, mode, status,
                       last_heartbeat, position_side, position_size, position_entry,
                       error_count, loop_count, total_trades, total_pnl, updated_at
                FROM bot_health
                ORDER BY symbol ASC
                """,
                conn,
            )
            # Fill missing optional columns so callers never KeyError
            for col in _EXPECTED_COLS:
                if col not in df.columns:
                    df[col] = None
            return df[_EXPECTED_COLS]
    except Exception:
        return pd.DataFrame(columns=_EXPECTED_COLS)


def get_consecutive_losses(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> int:
    """Count consecutive losses from the most recent trades."""
    closed = get_closed_trades(db_path, symbol=symbol)
    if closed.empty or "pnl" not in closed.columns:
        return 0

    count = 0
    for pnl in reversed(closed["pnl"].dropna().tolist()):
        if pnl < 0:
            count += 1
        else:
            break
    return count


def get_per_bot_summary(db_path: Optional[str] = None) -> pd.DataFrame:
    """Get per-symbol summary: trades, wins, PF, total_pnl, last_trade."""
    if not db_exists(db_path):
        return pd.DataFrame()

    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            """
            SELECT
                symbol,
                COUNT(*) as trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                ROUND(
                    CASE WHEN SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END) > 0
                    THEN SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END) /
                         SUM(CASE WHEN pnl < 0 THEN ABS(pnl) ELSE 0 END)
                    ELSE 0 END, 2
                ) as profit_factor,
                ROUND(SUM(pnl), 2) as total_pnl,
                MAX(timestamp) as last_trade
            FROM trades
            WHERE status = 'closed'
            GROUP BY symbol
            ORDER BY total_pnl DESC
            """,
            conn,
        )
        if not df.empty and "wins" in df.columns and "trades" in df.columns:
            df["win_rate"] = (df["wins"] / df["trades"] * 100).round(1)
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()
