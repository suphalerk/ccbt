"""SQLite helper functions to query trades.db for the dashboard."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# Default database path: check BOT_DATA_DIR first (Docker), then project root
_data_dir = os.getenv("BOT_DATA_DIR", "")
if _data_dir and Path(_data_dir, "trades.db").exists():
    DEFAULT_DB_PATH = Path(_data_dir) / "trades.db"
else:
    DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "trades.db"


def _get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get a read-only SQLite connection with row factory enabled.

    Opens with mode=ro (URI parameter) and sets PRAGMA query_only=1 so the
    dashboard process can never write to or lock the live trades.db.

    Args:
        db_path: Path to the database file. Defaults to trades.db in project root.

    Returns:
        sqlite3.Connection with Row factory, opened read-only.
    """
    path = db_path or str(DEFAULT_DB_PATH)
    # Use URI mode to request read-only access; falls back to plain connect if
    # the file doesn't exist yet so callers get a sensible error, not a URI one.
    uri = "file:{}?mode=ro".format(path.replace("?", "%3F"))
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        # File doesn't exist or not a valid DB yet — open normally so callers
        # that call db_exists() first get an empty result rather than a crash.
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    # Belt-and-suspenders: refuse all writes even if mode=ro wasn't honoured
    conn.execute("PRAGMA query_only=1")
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
    """Get cumulative PnL over time for equity curve plotting.

    Returns a DataFrame with columns:
      timestamp, pnl, cumulative_pnl, underwater

    ``underwater`` is the running-max drawdown series:
      underwater[i] = running_max(cumulative_pnl[0..i]) − cumulative_pnl[i]

    It is always <= 0 (0 at new equity highs, negative while in a drawdown).
    Computed server-side so TypeScript never re-derives financial math.
    """
    import numpy as np  # already available in the venv

    closed = get_closed_trades(db_path, symbol=symbol)
    if closed.empty or "pnl" not in closed.columns:
        return pd.DataFrame(columns=["timestamp", "pnl", "cumulative_pnl", "underwater"])

    df = closed[["timestamp", "pnl"]].copy()
    df["pnl"] = df["pnl"].fillna(0.0)
    df["cumulative_pnl"] = df["pnl"].cumsum()

    # Underwater = running_max − current_value  (always <= 0)
    running_max = np.maximum.accumulate(df["cumulative_pnl"].values)
    df["underwater"] = df["cumulative_pnl"].values - running_max  # always <= 0
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
            WHERE status = 'closed' AND COALESCE(close_reason, '') != 'orphan_reconcile'
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


# ---------------------------------------------------------------------------
# N9 — New Metrics Data Layer
# ---------------------------------------------------------------------------

#: Pattern to extract AUDITED_CONFIGS and FORWARD_TEST_CONFIGS from start.sh.
_STARTSH_CONFIG_RE = re.compile(
    r'^(?:AUDITED_CONFIGS|FORWARD_TEST_CONFIGS)="([^"]*)"',
    re.MULTILINE,
)

_START_SH_PATH = Path(__file__).resolve().parent.parent / "deploy" / "macos" / "start.sh"


def _parse_deployed_configs_from_startsh(project_root: Path) -> set:
    """Extract the set of config filenames from deploy/macos/start.sh.

    Reads the AUDITED_CONFIGS and FORWARD_TEST_CONFIGS shell variables.
    Falls back to an empty set if the file is missing or unparseable.

    Args:
        project_root: Project root — used to locate deploy/macos/start.sh.

    Returns:
        Set of config filenames (e.g. {'config.json', 'config_axsusdt_dualthrust.json'}).
    """
    start_sh = project_root / "deploy" / "macos" / "start.sh"
    if not start_sh.exists():
        return set()
    try:
        text = start_sh.read_text()
    except OSError:
        return set()

    filenames: set = set()
    for m in _STARTSH_CONFIG_RE.finditer(text):
        for fname in m.group(1).split():
            fname = fname.strip()
            if fname:
                filenames.add(fname)
    return filenames


#: Config filenames that are skipped (non-deployed, retired, or alternative exchange).
#: Used as fallback when deploy/macos/start.sh is absent (e.g. test fixtures).
_SKIP_CONFIGS: set = {
    "config_aggressive.json", "config_sniper.json", "config_yolo.json",
    "config_max.json",
    "config_doge.json",
    "config_sol_ichi.json",
    "config_gold.json",
    "config_gold_forex.json",
    "config_arb.json",
    "config_btc_ichi.json",
    "config_1000shibusdt_ichi.json",
    "config_trxusdt_ichi.json",
    "config_xlmusdt_ichi.json",
    "config_saharausdt_supertrend.json",
    "config_polusdt_ichi4htrail.json",
}


def _build_symbol_config_count(project_root: Path) -> Dict[str, int]:
    """Count how many deployed config files map to each symbol_clean.

    Primary source: deploy/macos/start.sh (AUDITED_CONFIGS + FORWARD_TEST_CONFIGS).
    This ensures retired configs that still exist on disk do not inflate counts.

    Fallback (when start.sh is absent, e.g. in test fixture directories):
    disk-glob of config_*.json minus _SKIP_CONFIGS — same behaviour as the
    original implementation.

    Symbols traded by multiple strategies (e.g. AXS with 4 configs) get
    count > 1 and therefore receive verdict = "MIXED" in get_trade_gate.

    Args:
        project_root: The project root directory containing config_*.json files
            and (for production) deploy/macos/start.sh.

    Returns:
        Dict mapping symbol_clean (e.g. "AXSUSDT") to the number of
        deployed config files that reference that symbol.
    """
    deployed = _parse_deployed_configs_from_startsh(project_root)

    # Fall back to disk glob when start.sh is absent (test fixtures / CI)
    if not deployed:
        for cfg_path in sorted(project_root.glob("config*.json")):
            if cfg_path.name not in _SKIP_CONFIGS:
                deployed.add(cfg_path.name)

    count_map: Dict[str, int] = {}
    for fname in sorted(deployed):
        cfg_path = project_root / fname
        if not cfg_path.exists():
            continue
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
        except Exception:
            continue
        symbol = cfg.get("symbol")
        if not symbol:
            continue
        symbol_clean = symbol.replace("/", "").replace(":", "")
        count_map[symbol_clean] = count_map.get(symbol_clean, 0) + 1
    return count_map


def get_close_reason_breakdown(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """Get breakdown of trade close reasons for closed, non-orphan trades.

    The breakdown is taxonomy-agnostic — auto-picks up any close_reason
    value present in the DB (including trail_stop, breakeven, etc.).

    Args:
        db_path: Path to trades.db.
        symbol: Optional symbol filter.

    Returns:
        DataFrame with columns: close_reason, count, pct, total_pnl, avg_pnl.
        pct values sum to ~100. Empty DataFrame if no data.
    """
    if not db_exists(db_path):
        return pd.DataFrame(columns=["close_reason", "count", "pct", "total_pnl", "avg_pnl"])

    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            f"""
            SELECT
                COALESCE(close_reason, 'unknown') AS close_reason,
                COUNT(*) AS count,
                COALESCE(SUM(pnl), 0.0) AS total_pnl,
                COALESCE(AVG(pnl), 0.0) AS avg_pnl
            FROM trades
            WHERE status = 'closed'
              AND COALESCE(close_reason, '') != 'orphan_reconcile'
              {filt}
            GROUP BY close_reason
            ORDER BY count DESC
            """,
            conn,
            params=params,
        )
        if df.empty:
            return pd.DataFrame(columns=["close_reason", "count", "pct", "total_pnl", "avg_pnl"])
        total = df["count"].sum()
        df["pct"] = (df["count"] / total * 100.0).round(2) if total > 0 else 0.0
        return df[["close_reason", "count", "pct", "total_pnl", "avg_pnl"]]
    except Exception:
        return pd.DataFrame(columns=["close_reason", "count", "pct", "total_pnl", "avg_pnl"])
    finally:
        conn.close()


def get_trade_gate(
    db_path: Optional[str] = None,
    min_trades: int = 15,
    graduate_pf: Optional[float] = None,
    project_root: Optional[Path] = None,
    since: Optional[str] = None,
    cohort_symbols: Optional[set] = None,
) -> Dict[str, Any]:
    """Compute per-symbol forward-test gate verdicts.

    Attribution guard: symbols traded by > 1 deployed config file get
    verdict = "MIXED" (the blended DB rows are not attributable to a single
    strategy). Single-config symbols get classified by classify() from
    api.classify.

    The ``since`` parameter mirrors the forward_test_report CLI's
    ``_live_stats(symbol, since=manifest['added'])`` filter so that the gate
    verdict matches what the CLI reports.  When ``cohort_symbols`` is provided,
    the ``since=`` filter is applied ONLY to those symbols — all other symbols
    use the full trade history.  This prevents the dashboard gate panel from
    appearing empty when the portfolio's historical trades all predate the
    manifest's ``added`` date (the common production state on day-1 of a new
    forward-test cohort).

    graduate_pf defaults to the value in research/forward_test_cohort.json
    if not provided (same source as the forward_test_report CLI).

    Args:
        db_path: Path to trades.db.
        min_trades: Minimum closed trades to meet the gate.
        graduate_pf: Profit factor threshold to graduate to READY_TO_AUDIT.
            Defaults to the manifest value (currently 1.3).
        project_root: Project root directory used to count config files.
            Defaults to the project root inferred from this file's location.
        since: ISO date string (e.g. "2026-06-07").  When set together with
            ``cohort_symbols``, only cohort symbol trades with
            ``timestamp >= since`` are counted; non-cohort symbols are
            unfiltered.  When ``cohort_symbols`` is None, the filter is applied
            globally (legacy behaviour, kept for direct CLI callers).
        cohort_symbols: Set of symbol strings (as they appear in the DB, e.g.
            ``{'ALICEUSDT', 'DASHUSDT'}``).  When provided, ``since=`` is
            scoped to these symbols only.

    Returns:
        Dict with:
          "rows": list of per-symbol dicts with keys:
            symbol, trades, win_rate, profit_factor, total_pnl, avg_pnl,
            reward_to_avgloss (or None), meets_min, verdict
          "summary": {n_meeting_min: int, n_total: int}
    """
    # Resolve project root
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    # Load graduate_pf from manifest if not provided
    if graduate_pf is None:
        manifest_path = project_root / "research" / "forward_test_cohort.json"
        try:
            manifest = json.loads(manifest_path.read_text())
            graduate_pf = float(manifest.get("graduate_pf", 1.3))
        except Exception:
            graduate_pf = 1.3

    # Import classify (canonical location in api/classify.py)
    from api.classify import classify  # noqa: PLC0415

    # Build attribution map: symbol_clean → config count
    count_map = _build_symbol_config_count(project_root)

    # Get all closed non-orphan trades
    closed = get_closed_trades(db_path)
    if closed.empty or "pnl" not in closed.columns:
        return {"rows": [], "summary": {"n_meeting_min": 0, "n_total": 0}}

    # Apply since= timestamp filter:
    # - When cohort_symbols is provided: filter ONLY those symbols by since=.
    #   Non-cohort symbols retain all historical trades so the gate panel is
    #   never empty when historical trades predate the manifest's added date.
    # - When cohort_symbols is None: apply globally (legacy / direct CLI usage).
    if since is not None and "timestamp" in closed.columns:
        if cohort_symbols is not None:
            # Normalise: strip exchange suffix to match DB symbol format
            cohort_norm = {s.replace("/", "").replace(":", "").upper() for s in cohort_symbols}
            # Build symbol column for comparison (same normalisation)
            sym_col = closed["symbol"].str.replace("/", "", regex=False).str.replace(":", "", regex=False).str.upper()
            is_cohort = sym_col.isin(cohort_norm)
            # Cohort rows: filter by since=; non-cohort rows: keep all
            cohort_mask = is_cohort & (closed["timestamp"] >= since)
            non_cohort_mask = ~is_cohort
            closed = closed[cohort_mask | non_cohort_mask]
        else:
            # Legacy: global filter (used by forward_test_report CLI callers)
            closed = closed[closed["timestamp"] >= since]
    if closed.empty:
        return {"rows": [], "summary": {"n_meeting_min": 0, "n_total": 0}}

    rows_out: List[Dict[str, Any]] = []

    for symbol, grp in closed.groupby("symbol"):
        pnls = grp["pnl"].dropna().values
        n = len(pnls)
        if n == 0:
            continue

        wins = pnls[pnls > 0]
        # Use <= 0 for losses to match forward_test_report._live_stats(symbol, since)
        # which uses: gross_l = abs(sum(p for p in pnls if p <= 0))
        # This ensures PF matches between the CLI report and the dashboard gate panel
        # when there are zero-pnl trades (e.g. break-even closes).
        losses = pnls[pnls <= 0]

        win_rate = float(len(wins) / n) if n > 0 else 0.0
        gross_profit = float(wins.sum()) if len(wins) > 0 else 0.0
        gross_loss = float(abs(losses.sum())) if len(losses) > 0 else 0.0
        pf: float
        if gross_loss > 0:
            pf = gross_profit / gross_loss
        elif gross_profit > 0:
            pf = float("inf")
        else:
            pf = 0.0

        total_pnl = float(pnls.sum())
        avg_pnl = float(pnls.mean()) if n > 0 else 0.0

        # reward_to_avgloss = mean(pnl) / mean(abs(losing pnl))
        # null for zero-loss symbols; suppressed for < ~10 trades
        # Uses <= 0 for losses to match _live_stats parity.
        reward_to_avgloss: Optional[float]
        strict_losses = pnls[pnls < 0]  # For mean-loss calc, only truly negative trades
        if len(strict_losses) > 0:
            reward_to_avgloss = avg_pnl / float(abs(strict_losses).mean())
        else:
            reward_to_avgloss = None

        meets_min = n >= min_trades

        # Attribution verdict
        symbol_clean = str(symbol).replace("/", "").replace(":", "")
        config_count = count_map.get(symbol_clean, 0)
        if config_count > 1:
            verdict = "MIXED"
        else:
            verdict = classify(n, pf, min_trades, graduate_pf)

        rows_out.append({
            "symbol": symbol,
            "trades": n,
            "config_count": config_count if config_count > 0 else 1,
            "win_rate": round(win_rate, 4),
            "profit_factor": pf,
            "total_pnl": round(total_pnl, 4),
            "avg_pnl": round(avg_pnl, 4),
            "reward_to_avgloss": round(reward_to_avgloss, 4) if reward_to_avgloss is not None else None,
            # real_r: avg_pnl / mean(|losing_pnl|) — same quantity as reward_to_avgloss,
            # kept as a distinct key so endpoint callers and the frontend have a stable
            # field that can later be replaced with a per-trade R-multiple if the DB
            # gains a risk_per_trade column.  Null only when there are zero losses.
            "real_r": round(reward_to_avgloss, 4) if reward_to_avgloss is not None else None,
            "meets_min": meets_min,
            "verdict": verdict,
        })

    n_meeting_min = sum(1 for r in rows_out if r["meets_min"])
    n_total = len(rows_out)

    return {
        "rows": rows_out,
        "summary": {"n_meeting_min": n_meeting_min, "n_total": n_total},
    }


def get_open_risk(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """Compute open position risk summary.

    Ships degraded: % risk gauge is omitted until bot writes balance to DB.
    null SL rows are counted separately as unprotected.

    Args:
        db_path: Path to trades.db.
        symbol: Optional symbol filter.

    Returns:
        Dict with:
          open_count: number of open trades
          notional: sum of abs(entry_price * size) for all open trades
          max_sl_loss: sum of abs(entry_price - stop_loss) * size for protected trades
          unprotected_count: number of open trades with null stop_loss
          pct: None (until balance is persisted — ships degraded)
    """
    empty: Dict[str, Any] = {
        "open_count": 0,
        "notional": 0.0,
        "max_sl_loss": 0.0,
        "unprotected_count": 0,
        "pct": None,
    }
    if not db_exists(db_path):
        return empty

    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            f"SELECT entry_price, size, stop_loss FROM trades WHERE status = 'open'{filt}",
            conn,
            params=params,
        )
    except Exception:
        return empty
    finally:
        conn.close()

    if df.empty:
        return empty

    open_count = len(df)
    notional = float((df["entry_price"].abs() * df["size"].abs()).sum())

    protected = df[df["stop_loss"].notna()].copy()
    unprotected_count = int((df["stop_loss"].isna()).sum())

    if len(protected) > 0:
        max_sl_loss = float(
            ((protected["entry_price"] - protected["stop_loss"]).abs() * protected["size"].abs()).sum()
        )
    else:
        max_sl_loss = 0.0

    return {
        "open_count": open_count,
        "notional": round(notional, 4),
        "max_sl_loss": round(max_sl_loss, 4),
        "unprotected_count": unprotected_count,
        "pct": None,  # ships degraded — no balance persisted yet
    }


def get_calendar_pnl(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
) -> pd.DataFrame:
    """Get PnL aggregated by UTC date for calendar heatmap display.

    Uses the same DATE(timestamp) SQL as get_daily_pnl so results reconcile
    exactly with the existing function.

    Args:
        db_path: Path to trades.db.
        symbol: Optional symbol filter.

    Returns:
        DataFrame with columns: date, daily_pnl, trades, wins, win_rate.
        win_rate is in [0, 1]. Empty DataFrame if no data.
    """
    _EMPTY_COLS = ["date", "daily_pnl", "trades", "wins", "win_rate"]
    if not db_exists(db_path):
        return pd.DataFrame(columns=_EMPTY_COLS)

    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            f"""
            SELECT
                DATE(timestamp) AS date,
                COALESCE(SUM(pnl), 0.0) AS daily_pnl,
                COUNT(*) AS trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins
            FROM trades
            WHERE status = 'closed'
              AND COALESCE(close_reason, '') != 'orphan_reconcile'
              {filt}
            GROUP BY DATE(timestamp)
            ORDER BY date ASC
            """,
            conn,
            params=params,
        )
    except Exception:
        return pd.DataFrame(columns=_EMPTY_COLS)
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=_EMPTY_COLS)

    df["win_rate"] = (df["wins"] / df["trades"]).where(df["trades"] > 0, 0.0)
    return df[_EMPTY_COLS]


def get_hour_dow_stats(
    db_path: Optional[str] = None,
    symbol: Optional[str] = None,
    bucket_hours: int = 4,
) -> pd.DataFrame:
    """Compute per (day-of-week, hour-bucket) trade statistics.

    UTC bucketing is done in pandas (not SQLite) to correctly handle
    timestamps with any timezone offset (e.g. +07:00 from OANDA/gold).
    SQLite strftime('%w', 'utc', ts) returns NULL for fractional-second
    timestamps, and DATE(ts, 'utc') double-shifts offset-aware timestamps.

    Args:
        db_path: Path to trades.db.
        symbol: Optional symbol filter.
        bucket_hours: Hour bucket size (default 4 → buckets 0, 4, 8, 12, 16, 20).

    Returns:
        DataFrame with columns: dow (0=Mon..6=Sun), hour_bucket, trades,
        avg_pnl, win_rate, total_pnl. Only cells with >= 1 trade are returned.
        Empty DataFrame if no data.
    """
    _EMPTY_COLS = ["dow", "hour_bucket", "trades", "avg_pnl", "win_rate", "total_pnl"]
    if not db_exists(db_path):
        return pd.DataFrame(columns=_EMPTY_COLS)

    filt, params = _symbol_filter(symbol)
    conn = _get_connection(db_path)
    try:
        df = pd.read_sql_query(
            f"""
            SELECT timestamp, pnl
            FROM trades
            WHERE status = 'closed'
              AND COALESCE(close_reason, '') != 'orphan_reconcile'
              AND pnl IS NOT NULL
              {filt}
            """,
            conn,
            params=params,
        )
    except Exception:
        return pd.DataFrame(columns=_EMPTY_COLS)
    finally:
        conn.close()

    if df.empty:
        return pd.DataFrame(columns=_EMPTY_COLS)

    # UTC bucketing in pandas — handles any timezone offset correctly
    t = pd.to_datetime(df["timestamp"], utc=True)
    df = df.copy()
    df["dow"] = t.dt.dayofweek  # Monday=0, Sunday=6
    df["hour_bucket"] = (t.dt.hour // bucket_hours) * bucket_hours

    grouped = df.groupby(["dow", "hour_bucket"])
    stats = grouped["pnl"].agg(
        trades="count",
        avg_pnl="mean",
        total_pnl="sum",
        wins=lambda x: (x > 0).sum(),
    ).reset_index()
    stats["win_rate"] = (stats["wins"] / stats["trades"]).where(stats["trades"] > 0, 0.0)

    # Rename: the lambda column is named by the lambda attr — reset to clean names
    # groupby agg with named aggregations may need rename depending on pandas version
    if "wins" not in stats.columns:
        # Fallback: compute wins separately
        wins_df = grouped["pnl"].apply(lambda x: (x > 0).sum()).reset_index(name="wins")
        stats = stats.merge(wins_df, on=["dow", "hour_bucket"])
        stats["win_rate"] = (stats["wins"] / stats["trades"]).where(stats["trades"] > 0, 0.0)

    return stats[_EMPTY_COLS]
