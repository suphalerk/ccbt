"""Test Price Action as confirmation filter on EMA crossover."""
import json
import sys
sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

signal_df = load_ohlcv("data/btcusdt_15m_2y.csv")
trend_df = load_ohlcv("data/btcusdt_1h_2y.csv")

with open("config.json") as f:
    base_config = json.load(f)

def run_test(name, config_overrides):
    config = json.loads(json.dumps(base_config))
    for key, value in config_overrides.items():
        if isinstance(value, dict) and key in config:
            config[key].update(value)
        else:
            config[key] = value
    engine = BacktestEngine(config, initial_balance=1000.0)
    return engine.run(signal_df, trend_df)

# For "filter" mode, we keep EMA crossover signals AND add PA signals.
# The PA signals are additional entry points that ALSO require EMA trend alignment.
# This effectively means: EMA crossover entries + PA trend-continuation entries = MORE trades

# Test B1: EMA + Pin Bar as ADDITIONAL signal source
# Both fire independently, but pin bar requires EMA trend alignment anyway
tests = {
    "Baseline (EMA only)": {},

    "B1: EMA + Pin Bar": {
        "signals": {
            "ema_crossover": {"enabled": True},
            "ema_fast_crossover": {"enabled": True},
            "ema_pullback": {"enabled": True},
            "pin_bar": {"enabled": True},
            "engulfing": {"enabled": False},
            "inside_bar_breakout": {"enabled": False},
        },
        "pin_bar_wick_ratio": 2.0,
        "pin_bar_ema_proximity_pct": 0.003,
        "pin_bar_volume_mult": 0.7,
    },

    "B2: EMA + Engulfing": {
        "signals": {
            "ema_crossover": {"enabled": True},
            "ema_fast_crossover": {"enabled": True},
            "ema_pullback": {"enabled": True},
            "pin_bar": {"enabled": False},
            "engulfing": {"enabled": True},
            "inside_bar_breakout": {"enabled": False},
        },
        "engulfing_body_ratio": 1.3,
        "engulfing_volume_mult": 1.0,
    },

    "B3: EMA + Inside Bar Breakout": {
        "signals": {
            "ema_crossover": {"enabled": True},
            "ema_fast_crossover": {"enabled": True},
            "ema_pullback": {"enabled": True},
            "pin_bar": {"enabled": False},
            "engulfing": {"enabled": False},
            "inside_bar_breakout": {"enabled": True},
        },
        "inside_bar_min_mother_range_atr_mult": 0.7,
        "inside_bar_breakout_volume_mult": 1.2,
    },

    "B4: EMA + All PA": {
        "signals": {
            "ema_crossover": {"enabled": True},
            "ema_fast_crossover": {"enabled": True},
            "ema_pullback": {"enabled": True},
            "pin_bar": {"enabled": True},
            "engulfing": {"enabled": True},
            "inside_bar_breakout": {"enabled": True},
        },
        "pin_bar_wick_ratio": 2.0,
        "pin_bar_ema_proximity_pct": 0.003,
        "pin_bar_volume_mult": 0.7,
        "engulfing_body_ratio": 1.3,
        "engulfing_volume_mult": 1.0,
        "inside_bar_min_mother_range_atr_mult": 0.7,
        "inside_bar_breakout_volume_mult": 1.2,
    },

    # B5: Relaxed PA parameters (more trades)
    "B5: EMA + All PA (relaxed)": {
        "signals": {
            "ema_crossover": {"enabled": True},
            "ema_fast_crossover": {"enabled": True},
            "ema_pullback": {"enabled": True},
            "pin_bar": {"enabled": True},
            "engulfing": {"enabled": True},
            "inside_bar_breakout": {"enabled": True},
        },
        "pin_bar_wick_ratio": 1.5,
        "pin_bar_ema_proximity_pct": 0.005,
        "pin_bar_volume_mult": 0.5,
        "pin_bar_min_range_atr_mult": 0.3,
        "engulfing_body_ratio": 1.2,
        "engulfing_min_body_pct": 0.35,
        "engulfing_volume_mult": 0.7,
        "inside_bar_min_mother_range_atr_mult": 0.5,
        "inside_bar_breakout_volume_mult": 0.8,
    },

    # B6: EMA crossover disabled, fast crossover disabled — ONLY PA signals
    # This tests if PA alone can replace EMA crossover
    "B6: PA Only (no crossover)": {
        "signals": {
            "ema_crossover": {"enabled": False},
            "ema_fast_crossover": {"enabled": False},
            "ema_pullback": {"enabled": False},
            "bb_breakout": {"enabled": False},
            "rsi_divergence": {"enabled": False},
            "pin_bar": {"enabled": True},
            "engulfing": {"enabled": True},
            "inside_bar_breakout": {"enabled": True},
        },
        "pin_bar_wick_ratio": 1.5,
        "pin_bar_ema_proximity_pct": 0.005,
        "pin_bar_volume_mult": 0.5,
        "engulfing_body_ratio": 1.2,
        "engulfing_volume_mult": 0.7,
        "inside_bar_min_mother_range_atr_mult": 0.5,
        "inside_bar_breakout_volume_mult": 0.8,
    },
}

print("\n" + "="*90)
print("HYPOTHESIS B: Price Action as ADDITIONAL Signal Source alongside EMA")
print("="*90)

results = {}
for name, overrides in tests.items():
    print(f"\n--- {name} ---")
    try:
        metrics = run_test(name, overrides)
        results[name] = metrics
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "="*90)
print("COMPARISON TABLE")
print("="*90)
print(f"{'Test':<35} {'Trades':>7} {'WR%':>6} {'PF':>6} {'DD%':>7} {'Sharpe':>7}")
print("-"*80)
for name, m in results.items():
    trades = getattr(m, 'total_trades', 0)
    wr = getattr(m, 'win_rate', 0) * 100
    pf = getattr(m, 'profit_factor', 0)
    dd = getattr(m, 'max_drawdown', 0) * 100
    sharpe = getattr(m, 'sharpe_ratio', 0)
    print(f"{name:<35} {trades:>7} {wr:>5.1f}% {pf:>6.2f} {dd:>6.1f}% {sharpe:>7.2f}")

# Per-source breakdown for the best combined variant
print("\n\nPer-source breakdown for B4 (EMA + All PA):")
# Re-run B4 and extract per-source metrics from the engine's trade list
config_b4 = json.loads(json.dumps(base_config))
config_b4["signals"].update({
    "pin_bar": {"enabled": True},
    "engulfing": {"enabled": True},
    "inside_bar_breakout": {"enabled": True},
})
config_b4["pin_bar_wick_ratio"] = 2.0
config_b4["pin_bar_ema_proximity_pct"] = 0.003
config_b4["pin_bar_volume_mult"] = 0.7
config_b4["engulfing_body_ratio"] = 1.3
config_b4["engulfing_volume_mult"] = 1.0
config_b4["inside_bar_min_mother_range_atr_mult"] = 0.7
config_b4["inside_bar_breakout_volume_mult"] = 1.2

engine_b4 = BacktestEngine(config_b4, initial_balance=1000.0)
metrics_b4 = engine_b4.run(signal_df, trend_df)

# Extract trades from engine state
trades_list = engine_b4.state.trades
sources = {}
for t in trades_list:
    src = t.signal_source
    if src not in sources:
        sources[src] = {"count": 0, "wins": 0, "pnl": 0, "gross_profit": 0, "gross_loss": 0}
    sources[src]["count"] += 1
    sources[src]["pnl"] += t.pnl
    if t.pnl > 0:
        sources[src]["wins"] += 1
        sources[src]["gross_profit"] += t.pnl
    else:
        sources[src]["gross_loss"] += abs(t.pnl)

print(f"{'Source':<25} {'Trades':>7} {'WR%':>6} {'PF':>6} {'PnL':>10}")
print("-"*60)
for src, data in sorted(sources.items()):
    wr = (data["wins"] / data["count"] * 100) if data["count"] > 0 else 0
    pf = (data["gross_profit"] / data["gross_loss"]) if data["gross_loss"] > 0 else float("inf")
    print(f"{src:<25} {data['count']:>7} {wr:>5.1f}% {pf:>6.2f} {data['pnl']:>+10.2f}")
