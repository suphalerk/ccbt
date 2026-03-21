"""
Comprehensive strategy sweep for 8 underperforming bots.

Tests all 6 strategy types (EMA 15m, Ichimoku 1H, Ichimoku 4H, 4H Trail,
Supertrend 1H, Vol Expansion 1H) and sweeps SL/TP/Trail params for winners.
"""

import copy
import json
import logging
import sys
import warnings
from pathlib import Path
from typing import Optional

import pandas as pd

# Suppress noise
logging.basicConfig(level=logging.WARNING)
warnings.filterwarnings("ignore")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.engine import BacktestEngine

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_csv(path: str) -> pd.DataFrame:
    """Load OHLCV CSV and return with DatetimeIndex (naive, no tz)."""
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p, index_col=0, parse_dates=True)
    # Strip timezone info to keep everything naive — avoids merge_asof tz mismatch
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.columns = [c.lower() for c in df.columns]
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"Missing column {col} in {path}")
    return df[["open", "high", "low", "close", "volume"]]


def slice_last_year(df: pd.DataFrame) -> pd.DataFrame:
    """Return only the last 12 months of data."""
    if df.empty:
        return df
    cutoff = df.index[-1] - pd.DateOffset(years=1)
    return df[df.index >= cutoff].copy()


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------

BASE_COMMON = {
    "risk_per_trade": 0.01,
    "leverage": 25,
    "signal_scorer": {"enabled": False},
    "ai_layer": {"enabled": False},
    "volume_mult": 1.0,
    "volume_max_mult": None,
    "atr_min": 0.0,
    "min_rr_ratio": 0,
    "max_daily_loss": 0.3,
    "max_positions": 2,
    "max_consecutive_losses": 5,
    "cooldown_hours": 1,
    "max_api_errors": 3,
    "use_testnet": True,
    "commission_rate": 0.0004,
    "slippage_rate": 0.00015,
    "weekend_trading_enabled": False,
    "regime_filter": {"enabled": True, "skip_ranging": True},
    "flexible_cooldown": {"enabled": False},
    "adaptive_sizing": {"enabled": False},
    "pyramiding": {"enabled": False},
    "mtd_accelerator": {"enabled": False},
    "trading_hours": {"enabled": True, "start_utc": 3, "end_utc": 20},
    "partial_tp_enabled": False,
    "move_sl_to_be_after_tp1": True,
    "breakeven_buffer_atr_mult": 0.5,
    "cooldown_candles_after_close": 0,
    "cooldown_candles_after_sl": 0,
}

ALL_SIGNALS_OFF = {
    "ema_crossover": {"enabled": False},
    "ema_fast_crossover": {"enabled": False},
    "ema_pullback": {"enabled": False},
    "rsi_divergence": {"enabled": False},
    "bb_breakout": {"enabled": False},
    "mean_reversion": {"enabled": False},
    "body_dominance": {"enabled": False},
    "squeeze_release": {"enabled": False},
    "ichimoku_cloud": {"enabled": False},
    "supertrend": {"enabled": False},
    "vol_expansion": {"enabled": False},
}


def _base_cfg(symbol: str) -> dict:
    cfg = copy.deepcopy(BASE_COMMON)
    cfg["symbol"] = symbol
    cfg["signals"] = copy.deepcopy(ALL_SIGNALS_OFF)
    # Fields needed by some signals
    cfg["ema_fast"] = 9
    cfg["ema_slow"] = 21
    cfg["ema_trend"] = 50
    cfg["ema_fast2"] = 5
    cfg["ema_slow2"] = 13
    cfg["rsi_period"] = 14
    cfg["atr_period"] = 14
    cfg["rsi_min"] = 45
    cfg["rsi_max"] = 65
    cfg["rsi_long_min"] = 45
    cfg["rsi_long_max"] = 65
    cfg["rsi_short_min"] = 35
    cfg["rsi_short_max"] = 55
    cfg["ema_slope_period"] = 5
    cfg["ema_slope_min"] = 0.01
    cfg["crossover_lookback"] = 2
    cfg["ichimoku_tenkan"] = 9
    cfg["ichimoku_kijun"] = 26
    cfg["ichimoku_senkou_b"] = 52
    cfg["supertrend_period"] = 10
    cfg["supertrend_multiplier"] = 3.0
    cfg["vol_expansion_threshold"] = 1.8
    cfg["vol_expansion_lookback"] = 10
    cfg["vol_expansion_atr_ma_period"] = 20
    cfg["bb_period"] = 20
    cfg["bb_std"] = 2.0
    cfg["swing_lookback"] = 5
    cfg["divergence_lookback"] = 20
    return cfg


def make_ema_cfg(symbol: str, sl: float = 1.0, tp: float = 3.0, trail: float = 2.0) -> dict:
    cfg = _base_cfg(symbol)
    cfg["timeframe_signal"] = "15m"
    cfg["timeframe_trend"] = "1h"
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_trail_mult_ranging"] = trail * 0.75
    cfg["atr_trail_mult_volatile"] = trail * 1.25
    cfg["atr_trail_mult_post_tp1"] = trail + 1.0
    cfg["signals"]["ema_crossover"]["enabled"] = True
    cfg["signals"]["ema_fast_crossover"]["enabled"] = True
    cfg["ema_slope_min"] = 0.01
    return cfg


def make_ichi1h_cfg(symbol: str, sl: float = 2.0, tp: float = 5.0, trail: float = 3.0) -> dict:
    cfg = _base_cfg(symbol)
    cfg["timeframe_signal"] = "1h"
    cfg["timeframe_trend"] = "1h"
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_trail_mult_ranging"] = trail * 0.67
    cfg["atr_trail_mult_volatile"] = trail * 1.33
    cfg["atr_trail_mult_post_tp1"] = trail + 1.0
    cfg["signals"]["ichimoku_cloud"]["enabled"] = True
    cfg["ema_slope_min"] = 0.02
    return cfg


def make_ichi4h_cfg(symbol: str, sl: float = 2.5, tp: float = 4.0, trail: float = 3.0) -> dict:
    cfg = _base_cfg(symbol)
    cfg["timeframe_signal"] = "4h"  # signal_data will be pre-resampled
    cfg["timeframe_trend"] = "4h"
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_trail_mult_ranging"] = trail * 0.67
    cfg["atr_trail_mult_volatile"] = trail * 1.33
    cfg["atr_trail_mult_post_tp1"] = trail + 1.0
    cfg["signals"]["ichimoku_cloud"]["enabled"] = True
    cfg["ema_slope_min"] = 0.02
    return cfg


def make_4htrail_cfg(symbol: str, sl: float = 1.5, tp: float = 0, trail: float = 4.0) -> dict:
    cfg = make_ichi4h_cfg(symbol, sl=sl, tp=tp, trail=trail)
    cfg["atr_tp_mult"] = tp  # 0 = trail-only
    return cfg


def make_supertrend_cfg(symbol: str, sl: float = 2.0, tp: float = 3.0, trail: float = 3.0) -> dict:
    cfg = _base_cfg(symbol)
    cfg["timeframe_signal"] = "1h"
    cfg["timeframe_trend"] = "1h"
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_trail_mult_ranging"] = trail * 0.67
    cfg["atr_trail_mult_volatile"] = trail * 1.33
    cfg["atr_trail_mult_post_tp1"] = trail + 1.0
    cfg["signals"]["supertrend"]["enabled"] = True
    return cfg


def make_volexp_cfg(symbol: str, sl: float = 2.5, tp: float = 3.0, trail: float = 3.0) -> dict:
    cfg = _base_cfg(symbol)
    cfg["timeframe_signal"] = "1h"
    cfg["timeframe_trend"] = "1h"
    cfg["atr_sl_mult"] = sl
    cfg["atr_tp_mult"] = tp
    cfg["atr_trail_mult"] = trail
    cfg["atr_trail_mult_trending"] = trail
    cfg["atr_trail_mult_ranging"] = trail * 0.67
    cfg["atr_trail_mult_volatile"] = trail * 1.33
    cfg["atr_trail_mult_post_tp1"] = trail + 1.0
    cfg["signals"]["vol_expansion"]["enabled"] = True
    return cfg


# ---------------------------------------------------------------------------
# Backtest runner
# ---------------------------------------------------------------------------

def run_backtest(
    cfg: dict,
    signal_df: pd.DataFrame,
    trend_df: Optional[pd.DataFrame] = None,
) -> Optional[dict]:
    """Run backtest, return result dict or None on error."""
    try:
        initial_balance = 10000.0
        engine = BacktestEngine(cfg, initial_balance=initial_balance)
        metrics = engine.run(signal_df, trend_df)
        trades = metrics.total_trades
        if trades == 0:
            return None
        # Compute total PnL% from monthly returns or trade pnl sum
        # BacktestMetrics has no total_return — derive from state balance
        final_balance = engine.state.balance
        total_return_pct = (final_balance - initial_balance) / initial_balance * 100.0
        return {
            "pf": round(metrics.profit_factor, 2),
            "wr": round(metrics.win_rate * 100, 1),
            "trades": trades,
            "sharpe": round(metrics.sharpe_ratio, 2),
            "dd": round(metrics.max_drawdown * 100, 1),
            "pnl": round(total_return_pct, 1),
        }
    except Exception as e:
        logging.warning(f"Backtest error: {e}")
        return None


def resample_4h(df: pd.DataFrame) -> pd.DataFrame:
    """Resample 1H OHLCV data to 4H."""
    return (
        df.resample("4h")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------

COINS = [
    ("POL",       "polusdt",       "Current: Ichi4H Trail PF 0.37"),
    ("TRX",       "trxusdt",       "Current: Ichi 1H PF 1.01"),
    ("ARB",       "arbusdt",       "Current: EMA 15m PF 1.03"),
    ("1000SHIB",  "1000shibusdt",  "Current: Ichi 1H PF 1.06"),
    ("DOGE",      "dogeusdt",      "Current: EMA 15m PF 1.11"),
    ("SOL",       "solusdt",       "Current: Ichi 1H PF 1.18"),
    ("SAHARA",    "saharausdt",    "Current: Supertrend 1H PF 1.27"),
    ("XLM",       "xlmusdt",       "Current: Ichi 1H PF 1.31"),
]

INITIAL_BALANCE = 10000.0


def run_coin_sweep(coin: str, prefix: str) -> list[dict]:
    """Run all 6 strategy types for a coin, return list of result dicts."""
    data_dir = Path(__file__).parent.parent / "data"

    # Load 15m data (for EMA)
    path_15m = data_dir / f"{prefix}_15m_2y.csv"
    # Fallback for 5y files (SOL)
    if not path_15m.exists():
        path_15m = data_dir / f"{prefix}_15m_5y.csv"
    df_15m_full = load_csv(str(path_15m))

    # Load 1h data (for Ichi, Supertrend, VolExp, and resample to 4h)
    path_1h = data_dir / f"{prefix}_1h_2y.csv"
    if not path_1h.exists():
        path_1h = data_dir / f"{prefix}_1h_5y.csv"
    df_1h_full = load_csv(str(path_1h))

    if df_1h_full.empty:
        print(f"  [SKIP] No 1H data found for {coin}")
        return []

    # Slice last year
    df_15m = slice_last_year(df_15m_full) if not df_15m_full.empty else pd.DataFrame()
    df_1h  = slice_last_year(df_1h_full)
    df_4h  = resample_4h(df_1h)

    symbol = f"{prefix.upper()}"
    results = []

    strategies = [
        ("EMA 15m",         lambda: make_ema_cfg(symbol)),
        ("Ichi 1H",         lambda: make_ichi1h_cfg(symbol)),
        ("Ichi 4H",         lambda: make_ichi4h_cfg(symbol)),
        ("4H Trail",        lambda: make_4htrail_cfg(symbol)),
        ("Supertrend 1H",   lambda: make_supertrend_cfg(symbol)),
        ("VolExp 1H",       lambda: make_volexp_cfg(symbol)),
    ]

    for strat_name, cfg_fn in strategies:
        cfg = cfg_fn()
        if strat_name == "EMA 15m":
            if df_15m.empty:
                results.append({"coin": coin, "strategy": strat_name, **_no_data()})
                continue
            r = run_backtest(cfg, df_15m, df_1h)
        elif strat_name in ("Ichi 1H", "Supertrend 1H", "VolExp 1H"):
            r = run_backtest(cfg, df_1h)
        elif strat_name in ("Ichi 4H", "4H Trail"):
            if len(df_4h) < 50:
                results.append({"coin": coin, "strategy": strat_name, **_no_data()})
                continue
            r = run_backtest(cfg, df_4h)

        if r is None:
            results.append({"coin": coin, "strategy": strat_name, **_no_data()})
        else:
            results.append({"coin": coin, "strategy": strat_name, **r})

    return results


def _no_data() -> dict:
    return {"pf": 0.0, "wr": 0.0, "trades": 0, "sharpe": 0.0, "dd": 0.0, "pnl": 0.0}


def run_param_sweep(
    coin: str,
    prefix: str,
    best_strategy: str,
) -> dict:
    """Sweep SL/TP/Trail for the best strategy of a coin."""
    data_dir = Path(__file__).parent.parent / "data"

    path_15m = data_dir / f"{prefix}_15m_2y.csv"
    if not path_15m.exists():
        path_15m = data_dir / f"{prefix}_15m_5y.csv"
    df_15m_full = load_csv(str(path_15m))

    path_1h = data_dir / f"{prefix}_1h_2y.csv"
    if not path_1h.exists():
        path_1h = data_dir / f"{prefix}_1h_5y.csv"
    df_1h_full = load_csv(str(path_1h))

    df_15m = slice_last_year(df_15m_full) if not df_15m_full.empty else pd.DataFrame()
    df_1h  = slice_last_year(df_1h_full)
    df_4h  = resample_4h(df_1h)

    symbol = prefix.upper()

    sl_values    = [1.0, 1.5, 2.0, 2.5, 3.0]
    tp_values    = [0, 3.0, 4.0, 5.0]
    trail_values = [2.0, 3.0, 4.0, 5.0]

    best_pf = 0.0
    best_params: dict = {}
    best_result: dict = {}

    for sl in sl_values:
        for tp in tp_values:
            for trail in trail_values:
                # Build config for this strategy type
                if best_strategy == "EMA 15m":
                    cfg = make_ema_cfg(symbol, sl=sl, tp=tp, trail=trail)
                    r = run_backtest(cfg, df_15m, df_1h) if not df_15m.empty else None
                elif best_strategy == "Ichi 1H":
                    cfg = make_ichi1h_cfg(symbol, sl=sl, tp=tp, trail=trail)
                    r = run_backtest(cfg, df_1h)
                elif best_strategy == "Ichi 4H":
                    cfg = make_ichi4h_cfg(symbol, sl=sl, tp=tp, trail=trail)
                    r = run_backtest(cfg, df_4h) if len(df_4h) >= 50 else None
                elif best_strategy == "4H Trail":
                    cfg = make_4htrail_cfg(symbol, sl=sl, tp=0, trail=trail)
                    r = run_backtest(cfg, df_4h) if len(df_4h) >= 50 else None
                elif best_strategy == "Supertrend 1H":
                    cfg = make_supertrend_cfg(symbol, sl=sl, tp=tp, trail=trail)
                    r = run_backtest(cfg, df_1h)
                elif best_strategy == "VolExp 1H":
                    cfg = make_volexp_cfg(symbol, sl=sl, tp=tp, trail=trail)
                    r = run_backtest(cfg, df_1h)
                else:
                    continue

                if r and r["trades"] >= 4 and r["pf"] > best_pf:
                    best_pf = r["pf"]
                    best_params = {"sl": sl, "tp": tp, "trail": trail}
                    best_result = r

    return {"params": best_params, "result": best_result}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def print_table(all_results: list[dict]) -> None:
    """Print formatted results table."""
    header = f"{'Coin':<12} {'Strategy':<16} {'PF':>6} {'WR%':>6} {'Trades':>7} {'Sharpe':>7} {'DD%':>6} {'PnL%':>7}"
    sep = "-" * len(header)
    print(sep)
    print(header)
    print(sep)
    last_coin = None
    for r in all_results:
        if r["coin"] != last_coin:
            if last_coin is not None:
                print()
            last_coin = r["coin"]
        trades = r["trades"]
        pf_str = f"{r['pf']:.2f}" if trades > 0 else "—"
        wr_str = f"{r['wr']:.1f}" if trades > 0 else "—"
        sh_str = f"{r['sharpe']:.2f}" if trades > 0 else "—"
        dd_str = f"{r['dd']:.1f}" if trades > 0 else "—"
        pnl_str = f"{r['pnl']:.1f}" if trades > 0 else "—"
        trades_str = str(trades) if trades > 0 else "0"
        # Highlight good results
        prefix_mark = "  "
        if trades >= 4 and r["pf"] >= 1.5:
            prefix_mark = ">>"
        elif trades >= 4 and r["pf"] >= 1.2:
            prefix_mark = " >"
        print(f"{prefix_mark}{r['coin']:<10} {r['strategy']:<16} {pf_str:>6} {wr_str:>6} {trades_str:>7} {sh_str:>7} {dd_str:>6} {pnl_str:>7}")
    print(sep)


def main() -> None:
    all_results: list[dict] = []
    recommendations: list[dict] = []

    print("\n" + "=" * 80)
    print("WEAK BOT STRATEGY SWEEP  —  Last 1 Year Data, 6 Strategy Types Each Coin")
    print("=" * 80)

    for coin, prefix, current_note in COINS:
        print(f"\n[{coin}]  {current_note}")
        results = run_coin_sweep(coin, prefix)
        all_results.extend(results)

        # Find best strategy (PF, min 4 trades)
        valid = [r for r in results if r["trades"] >= 4]
        if valid:
            best = max(valid, key=lambda x: x["pf"])
            recommendations.append({
                "coin": coin,
                "prefix": prefix,
                "best_strategy": best["strategy"],
                "pf": best["pf"],
                "trades": best["trades"],
            })
        else:
            recommendations.append({
                "coin": coin,
                "prefix": prefix,
                "best_strategy": "NONE",
                "pf": 0.0,
                "trades": 0,
            })

    print("\n\n" + "=" * 80)
    print("STRATEGY SWEEP RESULTS")
    print("=" * 80)
    print_table(all_results)

    # ---------------------------------------------------------------------------
    # Phase 2: Parameter sweep for promising strategies
    # ---------------------------------------------------------------------------
    print("\n\n" + "=" * 80)
    print("PARAMETER SWEEP  —  SL x TP x Trail for Best Strategy per Coin")
    print("=" * 80)

    optimised: list[dict] = []
    for rec in recommendations:
        if rec["best_strategy"] == "NONE" or rec["pf"] < 1.2:
            print(f"\n  {rec['coin']}: Skipping param sweep (best PF {rec['pf']:.2f} < 1.20 or no trades)")
            optimised.append({
                "coin": rec["coin"],
                "strategy": rec["best_strategy"],
                "pf": rec["pf"],
                "params": {},
                "result": {},
            })
            continue

        print(f"\n  {rec['coin']}: Sweeping {rec['best_strategy']} (base PF {rec['pf']:.2f}) ...")
        sweep = run_param_sweep(rec["coin"], rec["prefix"], rec["best_strategy"])
        if sweep["result"]:
            optimised.append({
                "coin": rec["coin"],
                "strategy": rec["best_strategy"],
                "pf": sweep["result"]["pf"],
                "params": sweep["params"],
                "result": sweep["result"],
            })
            p = sweep["params"]
            r = sweep["result"]
            print(f"    Best: SL={p['sl']} TP={p['tp']} Trail={p['trail']}  =>  "
                  f"PF {r['pf']:.2f} | WR {r['wr']:.1f}% | Trades {r['trades']} | "
                  f"Sharpe {r['sharpe']:.2f} | DD {r['dd']:.1f}% | PnL {r['pnl']:.1f}%")
        else:
            optimised.append({
                "coin": rec["coin"],
                "strategy": rec["best_strategy"],
                "pf": rec["pf"],
                "params": {},
                "result": {},
            })
            print(f"    No valid param found.")

    # ---------------------------------------------------------------------------
    # Final recommendations
    # ---------------------------------------------------------------------------
    print("\n\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)

    CURRENT_PF = {
        "POL":      0.37,
        "TRX":      1.01,
        "ARB":      1.03,
        "1000SHIB": 1.06,
        "DOGE":     1.11,
        "SOL":      1.18,
        "SAHARA":   1.27,
        "XLM":      1.31,
    }

    CURRENT_STRATEGY = {
        "POL":      "Ichi4H Trail",
        "TRX":      "Ichi 1H",
        "ARB":      "EMA 15m",
        "1000SHIB": "Ichi 1H",
        "DOGE":     "EMA 15m",
        "SOL":      "Ichi 1H",
        "SAHARA":   "Supertrend 1H",
        "XLM":      "Ichi 1H",
    }

    print(f"\n{'Coin':<12} {'Current':>12} {'Best Strategy':<16} {'Best PF':>8} {'SL':>5} {'TP':>5} {'Trail':>6} {'Action'}")
    print("-" * 90)
    for opt in optimised:
        coin = opt["coin"]
        cur_pf = CURRENT_PF.get(coin, 0)
        cur_strat = CURRENT_STRATEGY.get(coin, "?")
        best_pf = opt["pf"]
        strat = opt["strategy"]
        params = opt.get("params", {})
        sl_str    = f"{params['sl']:.1f}" if params else "—"
        tp_str    = f"{params['tp']:.1f}" if params else "—"
        trail_str = f"{params['trail']:.1f}" if params else "—"

        if best_pf >= 1.5:
            action = "REPLACE"
        elif best_pf >= 1.2:
            action = "consider"
        elif best_pf > cur_pf:
            action = "marginal +"
        else:
            action = "RETIRE"

        print(
            f"  {coin:<10} {cur_strat:>12} → {strat:<16} "
            f"PF {best_pf:>5.2f}  SL {sl_str:>4} TP {tp_str:>4} Trail {trail_str:>4}  {action}"
        )

    print("\n  Legend: >> = PF >= 1.5   > = PF >= 1.2")
    print("  REPLACE = clear winner vs current | consider = modest improvement | RETIRE = no edge found\n")


if __name__ == "__main__":
    main()
