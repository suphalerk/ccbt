"""Download 2 years of data and run backtest comparison: rigid vs flexible cooldown."""

import json
import logging
import sys
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger(__name__)

from backtest.data_loader import download_ohlcv, save_ohlcv, load_ohlcv
from backtest.engine import BacktestEngine

DATA_DIR = "data"
SIGNAL_FILE = os.path.join(DATA_DIR, "btcusdt_15m_2y.csv")
TREND_FILE = os.path.join(DATA_DIR, "btcusdt_1h_2y.csv")


def ensure_data():
    """Download 2 years of 15m and 1h data if not cached."""
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(SIGNAL_FILE):
        logger.info("Downloading 2 years of 15m data...")
        df_15m = download_ohlcv(symbol="BTC/USDT:USDT", timeframe="15m", months=24)
        save_ohlcv(df_15m, SIGNAL_FILE)
        logger.info(f"Saved {len(df_15m)} candles to {SIGNAL_FILE}")
    else:
        logger.info(f"Using cached {SIGNAL_FILE}")

    if not os.path.exists(TREND_FILE):
        logger.info("Downloading 2 years of 1h data...")
        df_1h = download_ohlcv(symbol="BTC/USDT:USDT", timeframe="1h", months=24)
        save_ohlcv(df_1h, TREND_FILE)
        logger.info(f"Saved {len(df_1h)} candles to {TREND_FILE}")
    else:
        logger.info(f"Using cached {TREND_FILE}")


def run_backtest(config, label):
    """Run backtest and return metrics."""
    signal_df = load_ohlcv(SIGNAL_FILE)
    trend_df = load_ohlcv(TREND_FILE)

    logger.info(f"\n{'='*60}")
    logger.info(f"  BACKTEST: {label}")
    logger.info(f"  Period: {signal_df.index[0]} → {signal_df.index[-1]}")
    logger.info(f"  Candles: {len(signal_df)} (15m), {len(trend_df)} (1h)")
    logger.info(f"{'='*60}")

    engine = BacktestEngine(config, initial_balance=10000.0)
    metrics = engine.run(signal_df, trend_df)
    return metrics


def main():
    with open("config.json") as f:
        config = json.load(f)

    # Step 1: Download data
    ensure_data()

    # Step 2: Run with flexible cooldown DISABLED (baseline)
    config_rigid = config.copy()
    config_rigid["flexible_cooldown"] = {
        "enabled": False,
        "min_quality_score": 0.7,
        "cooldown_reduction_factor": 0.5,
        "log_overrides": True,
    }
    metrics_rigid = run_backtest(config_rigid, "RIGID COOLDOWN (baseline)")

    # Step 3: Run with flexible cooldown ENABLED
    config_flex = config.copy()
    config_flex["flexible_cooldown"] = {
        "enabled": True,
        "min_quality_score": 0.7,
        "cooldown_reduction_factor": 0.5,
        "log_overrides": True,
    }
    metrics_flex = run_backtest(config_flex, "FLEXIBLE COOLDOWN (new)")

    # Step 4: Comparison
    print("\n" + "=" * 60)
    print("  COMPARISON: Rigid vs Flexible Cooldown")
    print("=" * 60)
    print(f"{'Metric':<25} {'Rigid':>12} {'Flexible':>12} {'Delta':>12}")
    print("-" * 61)

    comparisons = [
        ("Total Trades", metrics_rigid.total_trades, metrics_flex.total_trades),
        ("Win Rate %", round(metrics_rigid.win_rate * 100, 1), round(metrics_flex.win_rate * 100, 1)),
        ("Profit Factor", round(metrics_rigid.profit_factor, 2), round(metrics_flex.profit_factor, 2)),
        ("Avg R:R Actual", round(metrics_rigid.avg_rr_actual, 2), round(metrics_flex.avg_rr_actual, 2)),
        ("Max Drawdown %", round(metrics_rigid.max_drawdown * 100, 2), round(metrics_flex.max_drawdown * 100, 2)),
        ("Sharpe Ratio", round(metrics_rigid.sharpe_ratio, 2), round(metrics_flex.sharpe_ratio, 2)),
        ("Sortino Ratio", round(metrics_rigid.sortino_ratio, 2), round(metrics_flex.sortino_ratio, 2)),
    ]

    for name, rigid_val, flex_val in comparisons:
        if isinstance(rigid_val, (int, float)) and isinstance(flex_val, (int, float)):
            delta = flex_val - rigid_val
            sign = "+" if delta > 0 else ""
            delta_str = f"{sign}{delta}" if isinstance(delta, int) else f"{sign}{delta:.2f}"
        else:
            delta_str = "-"
        print(f"{name:<25} {str(rigid_val):>12} {str(flex_val):>12} {delta_str:>12}")

    print("=" * 60)


if __name__ == "__main__":
    main()
