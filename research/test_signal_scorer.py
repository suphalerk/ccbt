"""Test signal scorer Phase 1 — OHLCV scoring.

Runs:
1. Baseline (scorer disabled) — results must match standard config.
2. Threshold sweep: entry_threshold 0.05 → 0.60.

Usage:
    cd /Users/iceai/Work/ccbt
    python3 research/test_signal_scorer.py 2>&1 | grep -v "position_size_capped\|order_rejected\|^$"
"""

import json
import sys

sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.data_loader import load_ohlcv
from backtest.engine import BacktestEngine

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
signal_df = load_ohlcv("data/btcusdt_15m_2y.csv")
trend_df = load_ohlcv("data/btcusdt_1h_2y.csv")

with open("config.json") as f:
    base_config = json.load(f)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def run(overrides: dict):
    """Deep-merge overrides into base config and run backtest."""
    cfg = json.loads(json.dumps(base_config))  # Deep copy
    for k, v in overrides.items():
        if isinstance(v, dict) and k in cfg and isinstance(cfg[k], dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    engine = BacktestEngine(cfg, initial_balance=1000.0)
    metrics = engine.run(signal_df, trend_df)
    return metrics, engine.state.trades


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
print("=" * 70)
print("SIGNAL SCORER PHASE 1 — THRESHOLD SWEEP")
print("=" * 70)

# Baseline: scorer explicitly disabled
m_base, _ = run({"signal_scorer": {"enabled": False}})
base_trades = getattr(m_base, "total_trades", 0)
base_pf = getattr(m_base, "profit_factor", 0.0)
base_wr = getattr(m_base, "win_rate", 0.0) * 100
base_dd = getattr(m_base, "max_drawdown_pct", 0.0) * 100
base_sh = getattr(m_base, "sharpe_ratio", 0.0)
print(
    f"Baseline (scorer OFF): {base_trades}t, "
    f"WR {base_wr:.1f}%, PF {base_pf:.2f}, "
    f"DD {base_dd:.1f}%, Sharpe {base_sh:.2f}"
)

# Sweep entry_threshold values
thresholds = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]

print()
print(
    f"{'Threshold':>10} {'Trades':>7} {'WR%':>6} {'PF':>6} "
    f"{'DD%':>6} {'Sharpe':>7} {'Filtered%':>10}"
)
print("-" * 60)

for t in thresholds:
    high_conv = max(t + 0.20, 0.50)
    m, trades = run(
        {
            "signal_scorer": {
                "enabled": True,
                "entry_threshold": t,
                "high_conviction": high_conv,
            }
        }
    )
    tot = getattr(m, "total_trades", 0)
    wr = getattr(m, "win_rate", 0.0) * 100
    pf = getattr(m, "profit_factor", 0.0)
    dd = getattr(m, "max_drawdown_pct", 0.0) * 100
    sh = getattr(m, "sharpe_ratio", 0.0)
    filtered_pct = (1 - tot / base_trades) * 100 if base_trades > 0 else 0.0
    print(
        f"{t:>10.2f} {tot:>7} {wr:>5.1f}% {pf:>6.2f} "
        f"{dd:>5.1f}% {sh:>7.2f} {filtered_pct:>9.1f}%"
    )

print()
print(
    "Filtered%: fraction of baseline trades removed by scorer at this threshold."
)
print(
    "Look for threshold where PF improves without losing too many trades."
)
