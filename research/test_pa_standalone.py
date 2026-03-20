"""Test Price Action patterns as standalone signal sources."""
import json
import sys
sys.path.insert(0, "/Users/iceai/Work/ccbt")

from backtest.engine import BacktestEngine
from backtest.data_loader import load_ohlcv

# Load data
signal_df = load_ohlcv("data/btcusdt_15m_2y.csv")
trend_df = load_ohlcv("data/btcusdt_1h_2y.csv")

# Load base config
with open("config.json") as f:
    base_config = json.load(f)

def run_test(name, config_overrides):
    """Run a single backtest with config overrides."""
    config = json.loads(json.dumps(base_config))  # deep copy
    for key, value in config_overrides.items():
        if isinstance(value, dict) and key in config:
            config[key].update(value)
        else:
            config[key] = value

    engine = BacktestEngine(config, initial_balance=1000.0)
    metrics = engine.run(signal_df, trend_df)
    return metrics

# Test configurations
tests = {
    "Baseline (EMA only)": {},  # No changes

    "A1: Pin Bar Only": {
        "signals": {
            "ema_crossover": {"enabled": False},
            "ema_fast_crossover": {"enabled": False},
            "ema_pullback": {"enabled": False},
            "bb_breakout": {"enabled": False},
            "rsi_divergence": {"enabled": False},
            "mean_reversion": {"enabled": False},
            "body_dominance": {"enabled": False},
            "squeeze_release": {"enabled": False},
            "ichimoku_cloud": {"enabled": False},
            "pin_bar": {"enabled": True},
            "engulfing": {"enabled": False},
            "inside_bar_breakout": {"enabled": False},
        },
        "pin_bar_wick_ratio": 2.0,
        "pin_bar_close_position": 0.70,
        "pin_bar_max_upper_wick_pct": 0.30,
        "pin_bar_min_range_atr_mult": 0.5,
        "pin_bar_ema_proximity_pct": 0.003,
        "pin_bar_volume_mult": 0.7,
    },

    "A2: Engulfing Only": {
        "signals": {
            "ema_crossover": {"enabled": False},
            "ema_fast_crossover": {"enabled": False},
            "ema_pullback": {"enabled": False},
            "bb_breakout": {"enabled": False},
            "rsi_divergence": {"enabled": False},
            "mean_reversion": {"enabled": False},
            "body_dominance": {"enabled": False},
            "squeeze_release": {"enabled": False},
            "ichimoku_cloud": {"enabled": False},
            "pin_bar": {"enabled": False},
            "engulfing": {"enabled": True},
            "inside_bar_breakout": {"enabled": False},
        },
        "engulfing_body_ratio": 1.3,
        "engulfing_min_body_pct": 0.40,
        "engulfing_volume_mult": 1.0,
    },

    "A3: Inside Bar Breakout Only": {
        "signals": {
            "ema_crossover": {"enabled": False},
            "ema_fast_crossover": {"enabled": False},
            "ema_pullback": {"enabled": False},
            "bb_breakout": {"enabled": False},
            "rsi_divergence": {"enabled": False},
            "mean_reversion": {"enabled": False},
            "body_dominance": {"enabled": False},
            "squeeze_release": {"enabled": False},
            "ichimoku_cloud": {"enabled": False},
            "pin_bar": {"enabled": False},
            "engulfing": {"enabled": False},
            "inside_bar_breakout": {"enabled": True},
        },
        "inside_bar_min_mother_range_atr_mult": 0.7,
        "inside_bar_breakout_volume_mult": 1.2,
    },

    "A4: All PA Combined (no EMA)": {
        "signals": {
            "ema_crossover": {"enabled": False},
            "ema_fast_crossover": {"enabled": False},
            "ema_pullback": {"enabled": False},
            "bb_breakout": {"enabled": False},
            "rsi_divergence": {"enabled": False},
            "mean_reversion": {"enabled": False},
            "body_dominance": {"enabled": False},
            "squeeze_release": {"enabled": False},
            "ichimoku_cloud": {"enabled": False},
            "pin_bar": {"enabled": True},
            "engulfing": {"enabled": True},
            "inside_bar_breakout": {"enabled": True},
        },
        "pin_bar_wick_ratio": 2.0,
        "pin_bar_close_position": 0.70,
        "pin_bar_ema_proximity_pct": 0.003,
        "pin_bar_volume_mult": 0.7,
        "engulfing_body_ratio": 1.3,
        "engulfing_volume_mult": 1.0,
        "inside_bar_min_mother_range_atr_mult": 0.7,
        "inside_bar_breakout_volume_mult": 1.2,
    },
}

print("\n" + "="*80)
print("HYPOTHESIS A: Price Action as STANDALONE Signal Source")
print("="*80)

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

# Summary comparison table
print("\n" + "="*80)
print("COMPARISON TABLE")
print("="*80)
print(f"{'Test':<35} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Return%':>9} {'DD%':>7} {'Sharpe':>7}")
print("-"*80)
for name, m in results.items():
    trades = getattr(m, 'total_trades', 0)
    wr = getattr(m, 'win_rate', 0) * 100
    pf = getattr(m, 'profit_factor', 0)
    ret = getattr(m, 'total_return_pct', 0)
    dd = getattr(m, 'max_drawdown_pct', 0) * 100
    sharpe = getattr(m, 'sharpe_ratio', 0)
    print(f"{name:<35} {trades:>7} {wr:>5.1f}% {pf:>6.2f} {ret:>8.1f}% {dd:>6.1f}% {sharpe:>7.2f}")


# ── Parameter sweeps for the best performing standalone PA pattern ──────────
print("\n\n" + "="*80)
print("PARAMETER SWEEP: Pin Bar wick ratio")
print("="*80)
pin_bar_base = {
    "signals": {
        "ema_crossover": {"enabled": False},
        "ema_fast_crossover": {"enabled": False},
        "ema_pullback": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": False},
        "pin_bar": {"enabled": True},
        "engulfing": {"enabled": False},
        "inside_bar_breakout": {"enabled": False},
    },
    "pin_bar_ema_proximity_pct": 0.003,
    "pin_bar_volume_mult": 0.7,
}

sweep_wick = {}
for ratio in [1.5, 2.0, 2.5, 3.0]:
    label = f"Pin Bar wick_ratio={ratio}"
    print(f"\n--- {label} ---")
    try:
        overrides = json.loads(json.dumps(pin_bar_base))
        overrides["pin_bar_wick_ratio"] = ratio
        metrics = run_test(label, overrides)
        sweep_wick[label] = metrics
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "="*80)
print("SWEEP: Pin Bar — EMA proximity")
print("="*80)
sweep_prox = {}
for prox in [0.002, 0.003, 0.005, 0.010]:
    label = f"Pin Bar ema_proximity={prox}"
    print(f"\n--- {label} ---")
    try:
        overrides = json.loads(json.dumps(pin_bar_base))
        overrides["pin_bar_wick_ratio"] = 2.0
        overrides["pin_bar_ema_proximity_pct"] = prox
        metrics = run_test(label, overrides)
        sweep_prox[label] = metrics
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "="*80)
print("SWEEP: Engulfing — body ratio")
print("="*80)
engulfing_base = {
    "signals": {
        "ema_crossover": {"enabled": False},
        "ema_fast_crossover": {"enabled": False},
        "ema_pullback": {"enabled": False},
        "bb_breakout": {"enabled": False},
        "rsi_divergence": {"enabled": False},
        "mean_reversion": {"enabled": False},
        "body_dominance": {"enabled": False},
        "squeeze_release": {"enabled": False},
        "ichimoku_cloud": {"enabled": False},
        "pin_bar": {"enabled": False},
        "engulfing": {"enabled": True},
        "inside_bar_breakout": {"enabled": False},
    },
    "engulfing_volume_mult": 1.0,
}

sweep_eng_body = {}
for ratio in [1.2, 1.3, 1.5, 2.0]:
    label = f"Engulfing body_ratio={ratio}"
    print(f"\n--- {label} ---")
    try:
        overrides = json.loads(json.dumps(engulfing_base))
        overrides["engulfing_body_ratio"] = ratio
        metrics = run_test(label, overrides)
        sweep_eng_body[label] = metrics
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "="*80)
print("SWEEP: Engulfing — volume mult")
print("="*80)
sweep_eng_vol = {}
for vol in [0.7, 1.0, 1.3]:
    label = f"Engulfing volume_mult={vol}"
    print(f"\n--- {label} ---")
    try:
        overrides = json.loads(json.dumps(engulfing_base))
        overrides["engulfing_body_ratio"] = 1.3
        overrides["engulfing_volume_mult"] = vol
        metrics = run_test(label, overrides)
        sweep_eng_vol[label] = metrics
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

# Final consolidated sweep table
print("\n\n" + "="*80)
print("SWEEP RESULTS — ALL")
print("="*80)
print(f"{'Test':<40} {'Trades':>7} {'WR%':>6} {'PF':>6} {'Return%':>9} {'DD%':>7} {'Sharpe':>7}")
print("-"*80)

all_sweeps = {**sweep_wick, **sweep_prox, **sweep_eng_body, **sweep_eng_vol}
for name, m in all_sweeps.items():
    trades = getattr(m, 'total_trades', 0)
    wr = getattr(m, 'win_rate', 0) * 100
    pf = getattr(m, 'profit_factor', 0)
    ret = getattr(m, 'total_return_pct', 0)
    dd = getattr(m, 'max_drawdown_pct', 0) * 100
    sharpe = getattr(m, 'sharpe_ratio', 0)
    print(f"{name:<40} {trades:>7} {wr:>5.1f}% {pf:>6.2f} {ret:>8.1f}% {dd:>6.1f}% {sharpe:>7.2f}")
